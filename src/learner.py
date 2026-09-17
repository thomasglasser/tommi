import json
import logging
import time
from typing import Dict, Any, Optional, List
from github import Github, GithubException
from github.PullRequest import PullRequest
from google import genai
from google.genai import types

from src.config import TommiConfig
from src.rules_loader import load_all_rules
from src.models_resolver import resolve_candidate_models, resolve_model_name
from src.reviewer import QuotaExceededException, HighDemandException, extract_retry_delay

logger = logging.getLogger("tommi.learner")


class TommiLearner:
    def __init__(self, config: TommiConfig, github_client: Github, tommi_client: Optional[Github] = None):
        self.config = config
        self.g = github_client
        self.tommi_g = tommi_client or github_client
        self.client = genai.Client(api_key=config.gemini_api_key)

    def _parse_json_dict(self, raw_text: str) -> Dict[str, Any]:
        """Parses a JSON dictionary from response text, handling fences and prose."""
        if not raw_text or not raw_text.strip():
            raise ValueError("Empty response text")

        text = raw_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            data = json.loads(text, strict=False)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

        # Extract {...} substring if enclosed in prose
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            try:
                data = json.loads(text[start_idx:end_idx + 1], strict=False)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

        raise ValueError(f"Unable to parse JSON dictionary from: {text[:200]}...")

    def _extract_response_text(self, response: Any) -> str:
        """Extracts text from response, excluding thought parts and handling candidate structure."""
        if not response:
            return ""
        candidate = response.candidates[0] if (hasattr(response, "candidates") and response.candidates) else None
        if not candidate:
            return response.text.strip() if hasattr(response, "text") and response.text else ""

        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) if content else None
        if parts:
            text_parts = []
            for part in parts:
                is_thought = bool(getattr(part, "thought", False))
                p_text = getattr(part, "text", None)
                if isinstance(p_text, str) and p_text and not is_thought:
                    text_parts.append(p_text)
            if text_parts:
                return "".join(text_parts).strip()

        return response.text.strip() if hasattr(response, "text") and response.text else ""

    def _generate_content_with_fallback(self, prompt: str, response_mime_type: str = "application/json") -> str:
        """Executes content generation, trying candidate models with backoff on 503/429 before failing."""
        candidate_models = resolve_candidate_models(self.client, self.config.model_name)
        response = None
        last_error = None
        encountered_429 = False
        encountered_503 = False

        for i, model_name in enumerate(candidate_models):
            logger.info(f"Running Gemini learning generation with model '{model_name}'...")
            max_attempts = 2
            for attempt in range(max_attempts):
                try:
                    gen_config = types.GenerateContentConfig(
                        temperature=0.1,
                        response_mime_type=response_mime_type,
                        max_output_tokens=65536,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    )
                    if getattr(self.config, "thinking_budget", 0) > 0:
                        gen_config.thinking_config = types.ThinkingConfig(
                            thinking_budget=self.config.thinking_budget
                        )

                    response = self.client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=gen_config,
                    )
                    break
                except Exception as e:
                    error_str = str(e).lower()
                    last_error = e

                    if getattr(gen_config, "thinking_config", None) and ("thinking" in error_str or "unsupported" in error_str):
                        logger.info(f"Learner model '{model_name}' does not support thinking_config. Retrying without thinking_config...")
                        gen_config.thinking_config = None
                        try:
                            response = self.client.models.generate_content(
                                model=model_name,
                                contents=prompt,
                                config=gen_config,
                            )
                            break
                        except Exception as thinking_retry_err:
                            e = thinking_retry_err
                            error_str = str(e).lower()
                            last_error = e

                    is_503 = "503" in error_str or "high demand" in error_str or "unavailable" in error_str or "overloaded" in error_str
                    is_429 = "429" in error_str or "quota" in error_str or "exhausted" in error_str or "resourceexhausted" in error_str or "rate limit" in error_str or "too many requests" in error_str

                    if is_503:
                        encountered_503 = True
                    if is_429:
                        encountered_429 = True

                    if is_503 or is_429:
                        retry_delay = extract_retry_delay(e)
                        if retry_delay is not None and retry_delay <= 15:
                            backoff_sec = retry_delay + 1
                            if attempt < max_attempts - 1:
                                logger.warning(
                                    f"Learner model '{model_name}' encountered {'high demand (503)' if is_503 else 'rate limit (429)'} on attempt {attempt + 1}. "
                                    f"Backing off for {backoff_sec:.1f}s before retry..."
                                )
                                time.sleep(backoff_sec)
                                continue
                            else:
                                logger.warning(f"Learner model '{model_name}' exhausted retries on {'503 high demand' if is_503 else '429 rate limit'}.")
                                break
                        elif retry_delay is not None and retry_delay > 15:
                            logger.warning(
                                f"Learner model '{model_name}' encountered {'high demand (503)' if is_503 else 'rate limit (429)'}: {e}. "
                                f"Recommended retryDelay of {retry_delay:.1f}s exceeds short backoff. Failing over to next model immediately..."
                            )
                            break
                        else:
                            if attempt < max_attempts - 1:
                                backoff_sec = (attempt + 1) * 5
                                logger.warning(
                                    f"Learner model '{model_name}' encountered {'high demand (503)' if is_503 else 'rate limit (429)'} on attempt {attempt + 1}. "
                                    f"Backing off for {backoff_sec}s before retry..."
                                )
                                time.sleep(backoff_sec)
                                continue
                            else:
                                logger.warning(f"Learner model '{model_name}' exhausted retries on {'503 high demand' if is_503 else '429 rate limit'}.")
                                break
                    else:
                        logger.warning(f"Failed to generate AI learning response with model '{model_name}': {e}")
                        break

            if response:
                break
            elif i < len(candidate_models) - 1:
                if encountered_429 or encountered_503:
                    time.sleep(3)
                logger.info(f"Retrying with fallback model '{candidate_models[i + 1]}'...")

        if not response:
            if encountered_429:
                raise QuotaExceededException("T.O.M.M.I. has run out of AI API quota / rate limit for today.")
            elif encountered_503:
                raise HighDemandException("T.O.M.M.I. is currently experiencing high demand. Please try again in a few moments.")
            elif last_error:
                raise RuntimeError(f"Failed to generate AI learning response: {last_error}") from last_error
            raise RuntimeError("Failed to obtain response from Gemini API.")

        return self._extract_response_text(response)

    def learn_from_merged_pr(
        self,
        pr: PullRequest,
        pr_diff: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Analyzes human review comments made during a merged PR, synthesizes any new coding rules/patterns,
        and opens a rule proposal PR on thomasglasser/tommi if actionable lessons are discovered.
        """
        logger.info(f"Analyzing review comments on merged PR #{pr.number} for rule learning...")

        # 1. Collect all human comments from review comments, reviews, and issue comments
        raw_comments: List[Dict[str, Any]] = []

        # Inline diff review comments
        try:
            for rc in pr.get_review_comments():
                user_login = rc.user.login if rc.user else ""
                user_type = getattr(rc.user, "type", "")
                if user_type == "Bot" or user_login.endswith("[bot]") or user_login in ("t-o-m-m-i-ai-reviewer", "github-actions"):
                    continue
                body = (rc.body or "").strip()
                if not body or body.startswith("/tommi"):
                    continue
                raw_comments.append({
                    "author": user_login,
                    "type": "inline_review_comment",
                    "path": rc.path,
                    "line": rc.line or getattr(rc, "original_line", None),
                    "diff_hunk": getattr(rc, "diff_hunk", ""),
                    "body": body
                })
        except Exception as e:
            logger.warning(f"Failed to fetch review comments: {e}")

        # Submitted reviews (top-level review summaries)
        try:
            for rev in pr.get_reviews():
                user_login = rev.user.login if rev.user else ""
                user_type = getattr(rev.user, "type", "")
                if user_type == "Bot" or user_login.endswith("[bot]") or user_login in ("t-o-m-m-i-ai-reviewer", "github-actions"):
                    continue
                body = (rev.body or "").strip()
                if not body or body.startswith("/tommi"):
                    continue
                raw_comments.append({
                    "author": user_login,
                    "type": "review_summary",
                    "state": rev.state,
                    "body": body
                })
        except Exception as e:
            logger.warning(f"Failed to fetch reviews: {e}")

        # PR conversation comments
        try:
            for ic in pr.get_issue_comments():
                user_login = ic.user.login if ic.user else ""
                user_type = getattr(ic.user, "type", "")
                if user_type == "Bot" or user_login.endswith("[bot]") or user_login in ("t-o-m-m-i-ai-reviewer", "github-actions"):
                    continue
                body = (ic.body or "").strip()
                if not body or body.startswith("/tommi"):
                    continue
                raw_comments.append({
                    "author": user_login,
                    "type": "conversation_comment",
                    "body": body
                })
        except Exception as e:
            logger.warning(f"Failed to fetch issue comments: {e}")

        if not raw_comments:
            logger.info(f"No maintainer review comments found on merged PR #{pr.number}. Nothing to learn.")
            return None

        logger.info(f"Found {len(raw_comments)} maintainer review comment(s) to analyze.")

        # Format comments for Gemini
        formatted_comments = []
        for c in raw_comments:
            if c["type"] == "inline_review_comment":
                formatted_comments.append(
                    f"- **Author @{c['author']} on `{c['path']}:{c.get('line', '?')}`**:\n"
                    f"  Diff context:\n```\n{c.get('diff_hunk', '')[:300]}\n```\n"
                    f"  Comment: \"{c['body']}\""
                )
            else:
                formatted_comments.append(
                    f"- **Author @{c['author']} ({c['type']})**:\n"
                    f"  Comment: \"{c['body']}\""
                )
        comments_text = "\n\n".join(formatted_comments)

        rules = load_all_rules()

        prompt = f"""
You are analyzing code review feedback made by repository maintainers (such as Thomas Glasser) on a newly MERGED Pull Request.
Your goal is to identify if the maintainers taught or enforced any reusable coding standards, Minecraft/NeoForge patterns, performance rules, architecture conventions, or review guidelines that should be added to T.O.M.M.I.'s central rule set.

### EXISTING RULES:
{rules.format_for_prompt()}

### MERGED PR CONTEXT:
- Repository: {self.config.github_repository}
- PR: #{pr.number} - {pr.title}
- Description: {pr.body or 'None'}

### MAINTAINER'S REVIEW COMMENTS:
{comments_text}

### MERGED PR DIFF SNIPPET:
```diff
{pr_diff[:4000]}
```

### INSTRUCTIONS:
1. Examine the maintainer's review comments to see if any comment states a general, reusable coding standard, bug prevention technique, or preference (e.g. "Do not use X", "Always check Y", "FastUtil should be used here", etc.).
2. Check if the feedback is ALREADY covered by the existing rules. If it is already covered or just conversational/PR-specific (e.g. "looks good", "thanks"), DO NOT propose a duplicate rule.
3. If NO new rules or modifications are warranted, return:
   {{"has_new_rules": false, "reason": "No new generalizable rules found in comments"}}
4. If new or refined rules ARE discovered, return:
   {{
     "has_new_rules": true,
     "target_file": "rules/minecraft.md",
     "section_header": "## Appropriate Section Header",
     "rule_markdown": "* **Rule Title**: Concise imperative rule specification.",
     "summary": "1-sentence summary of the new/updated rule",
     "rationale": "Detailed explanation citing the maintainer's comments and PR context",
     "source_comments": ["Quote of comment 1", "Quote of comment 2"]
   }}
"""

        raw_response = self._generate_content_with_fallback(prompt)

        try:
            plan = self._parse_json_dict(raw_response)
        except Exception as e:
            logger.error(f"Failed to parse learning JSON from Gemini: {e}")
            return None

        if not plan.get("has_new_rules"):
            logger.info(f"No new actionable rules found from merged PR #{pr.number} comments: {plan.get('reason', 'N/A')}")
            return None

        logger.info(f"Learned rule proposal from merged PR #{pr.number}: {plan.get('summary')}")
        source_summary = "\n".join([f"> {s}" for s in plan.get("source_comments", [])]) or f"Review comments on merged PR #{pr.number}"
        pr_url = self._create_rule_pr(plan, source_summary)

        return {
            "learning_plan": plan,
            "pr_url": pr_url
        }

    def process_feedback(
        self,
        command_type: str,
        feedback_text: str,
        pr_title: str,
        pr_diff: str,
        thread_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Synthesizes a rule update from maintainer feedback and opens a PR on the TOMMI repo.
        """
        logger.info(f"Processing learning feedback for command '{command_type}'...")
        rules = load_all_rules()

        # Build prompt for Gemini to determine the appropriate rule change
        prompt = f"""
You are assisting Thomas Glasser in maintaining and improving the rules for T.O.M.M.I. (his automated code review bot).
Thomas has provided review feedback / correction on a Pull Request.

### EXISTING RULES:
{rules.format_for_prompt()}

### REPOSITORY & PR CONTEXT:
- Repository: {self.config.github_repository}
- PR: #{self.config.pr_number} - {pr_title}

### FEEDBACK / INSTRUCTION:
- Command Type: {command_type}
- Feedback Text: {feedback_text}
- Thread / Comment Context: {thread_context or 'None provided'}

### RELEVANT DIFF SNIPPET:
```diff
{pr_diff[:4000]}
```

### INSTRUCTIONS:
1. Analyze Thomas's feedback to understand the rule refinement, false-positive prevention, or new standard he wants.
2. Select the most appropriate rule file to modify among: `rules/core.md`, `rules/java.md`, `rules/minecraft.md`, `rules/performance.md` (or propose a new one if necessary).
3. Formulate the precise rule text using Thomas's strict, imperative style (**MUST**, **NEVER**, **ALWAYS**, concise bullet points).
4. Return a JSON object with:
   - `target_file`: e.g. "rules/core.md", "rules/java.md", "rules/minecraft.md", or "rules/performance.md".
   - `update_type`: "append" (adds a new bullet point to a section) or "replace" or "new_section".
   - `section_header`: The section under which to add the rule (e.g., "## 1. Naming & Terminology" or "## 2. Language Features & APIs").
   - `rule_markdown`: The exact bullet point(s) to add or update.
   - `summary`: A concise 1-sentence summary of the rule change.
   - `rationale`: Explanation of why this rule was learned from the feedback.
"""

        raw_response = self._generate_content_with_fallback(prompt)
        learning_plan = self._parse_json_dict(raw_response)
        logger.info(f"Learned rule proposal: {learning_plan.get('summary')}")

        # Create PR on the central TOMMI repo
        pr_url = self._create_rule_pr(learning_plan, feedback_text)
        return {
            "learning_plan": learning_plan,
            "pr_url": pr_url
        }

    def _refactor_and_integrate_rule(self, current_text: str, target_file_path: str, plan: Dict[str, Any]) -> str:
        """
        Uses Gemini to cleanly refactor and integrate a new or updated rule into the existing markdown document,
        merging with existing bullet points where applicable and eliminating duplicate or contradictory statements.
        Aborts with an error if model synthesis fails or truncates, preventing messy unvetted heading-append pollution.
        """
        rule_markdown = plan.get("rule_markdown", "").strip()
        section_header = plan.get("section_header", "")
        summary = plan.get("summary", "")
        rationale = plan.get("rationale", "")

        prompt = f"""You are an expert technical editor maintaining rule documentation for T.O.M.M.I. (an automated code reviewer).
Cleanly integrate the following rule update into the rule document `{target_file_path}`.

### CURRENT COMPLETE DOCUMENT CONTENT:
```markdown
{current_text}
```

### NEW RULE TO INTEGRATE:
- Target Section: {section_header}
- Proposed Rule Text: {rule_markdown}
- Summary of Change: {summary}
- Rationale: {rationale}

### INSTRUCTIONS:
1. Integrate the rule cleanly into `{target_file_path}`.
2. If an existing bullet point in that section already covers or addresses this topic, MERGE, REFINE, or EXPAND that bullet point in place instead of creating a duplicate bullet point.
3. If it is a new rule, place it in the most logical position within the section.
4. Remove any duplicate bullet points, contradictions, or erratic formatting in that section.
5. Preserve all existing markdown formatting, titles, headers, and strict imperative style (**MUST**, **NEVER**, **ALWAYS**).
6. Return ONLY the complete updated raw markdown document text. Do not wrap in backticks or markdown fences, and do not add conversational preamble.
"""
        try:
            raw_response = self._generate_content_with_fallback(prompt, response_mime_type="text/plain")
            refactored = self._extract_response_text(raw_response) if hasattr(raw_response, "candidates") else str(raw_response).strip()

            # Strip code fences if the model still wrapped them
            if refactored.startswith("```markdown"):
                refactored = refactored[11:].strip()
            elif refactored.startswith("```"):
                refactored = refactored[3:].strip()
            if refactored.endswith("```"):
                refactored = refactored[:-3].strip()

            # Safety check: ensure response is substantive and didn't hallucinate or truncate
            if len(refactored) >= len(current_text) * 0.6 and ("# " in refactored or "## " in refactored):
                logger.info(f"Successfully synthesized clean, refactored rule document for '{target_file_path}'.")
                return refactored.rstrip() + "\n"
            else:
                logger.warning(
                    f"Refactored document failed safety validation (len={len(refactored)} vs orig={len(current_text)})."
                )
                raise ValueError("Synthesized rule document failed safety validation (possible truncation or missing headers).")
        except Exception as e:
            logger.error(f"Failed to cleanly synthesize and refactor rule document with Gemini: {e}")
            raise RuntimeError(
                f"Could not cleanly refactor and integrate rule into '{target_file_path}'. "
                f"Aborting update to prevent messy unformatted appends: {e}"
            ) from e

    def _create_rule_pr(self, plan: Dict[str, Any], raw_feedback: str) -> str:
        """
        Creates a new branch and Pull Request on the central TOMMI repository.
        """
        tommi_repo_name = self.config.tommi_repo
        logger.info(f"Opening PR on central repository '{tommi_repo_name}'...")

        try:
            tommi_repo = self.tommi_g.get_repo(tommi_repo_name)
        except GithubException as e:
            logger.error(f"Failed to access TOMMI repo '{tommi_repo_name}': {e}")
            raise

        default_branch = tommi_repo.default_branch
        base_ref = tommi_repo.get_branch(default_branch)
        
        timestamp = int(time.time())
        branch_name = f"learn/pr-{self.config.pr_number}-{timestamp}"

        # Create new branch
        tommi_repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_ref.commit.sha)

        target_file_path = plan.get("target_file", "rules/core.md")
        rule_markdown = plan.get("rule_markdown", "").strip()
        summary = plan.get("summary", "Update rules from feedback")
        rationale = plan.get("rationale", "")

        # Get existing file content
        try:
            file_content_obj = tommi_repo.get_contents(target_file_path, ref=branch_name)
            current_text = file_content_obj.decoded_content.decode("utf-8")
            file_sha = file_content_obj.sha
        except GithubException:
            current_text = f"# {target_file_path}\n\n"
            file_sha = None

        # Cleanly refactor and integrate rule into current_text
        updated_text = self._refactor_and_integrate_rule(current_text, target_file_path, plan)


        commit_msg = f"learn: {summary}"
        if file_sha:
            tommi_repo.update_file(
                path=target_file_path,
                message=commit_msg,
                content=updated_text,
                sha=file_sha,
                branch=branch_name
            )
        else:
            tommi_repo.create_file(
                path=target_file_path,
                message=commit_msg,
                content=updated_text,
                branch=branch_name
            )

        # Open Pull Request
        pr_title = f"learn: {summary}"
        pr_body = f"""### 🤖 T.O.M.M.I. Autonomous Rule Proposal

**Source**: Feedback on [{self.config.github_repository}#{self.config.pr_number}](https://github.com/{self.config.github_repository}/pull/{self.config.pr_number})
**Feedback**:
> {raw_feedback}

### Summary of Change
{summary}

### Rationale
{rationale}

### Modified Rule File
- `{target_file_path}`

---
*Auto-generated by T.O.M.M.I. Feedback Learning Engine*
"""
        created_pr = tommi_repo.create_pull(
            title=pr_title,
            body=pr_body,
            base=default_branch,
            head=branch_name
        )

        logger.info(f"Successfully created PR #{created_pr.number}: {created_pr.html_url}")
        return created_pr.html_url
