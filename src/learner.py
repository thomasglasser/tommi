import json
import logging
import time
from typing import Dict, Any, Optional
from github import Github, GithubException
from google import genai
from google.genai import types

from src.config import TommiConfig
from src.rules_loader import load_all_rules
from src.models_resolver import resolve_model_name

logger = logging.getLogger("tommi.learner")


class TommiLearner:
    def __init__(self, config: TommiConfig, github_client: Github, tommi_client: Optional[Github] = None):
        self.config = config
        self.g = github_client
        self.tommi_g = tommi_client or github_client
        self.client = genai.Client(api_key=config.gemini_api_key)

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
        model_name = resolve_model_name(self.client, self.config.model_name)

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

        response = self.client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            )
        )

        raw_json = response.text.strip()
        if raw_json.startswith("```json"):
            raw_json = raw_json[7:]
        if raw_json.startswith("```"):
            raw_json = raw_json[3:]
        if raw_json.endswith("```"):
            raw_json = raw_json[:-3]

        learning_plan = json.loads(raw_json.strip())
        logger.info(f"Learned rule proposal: {learning_plan.get('summary')}")

        # Create PR on the central TOMMI repo
        pr_url = self._create_rule_pr(learning_plan, feedback_text)
        return {
            "learning_plan": learning_plan,
            "pr_url": pr_url
        }

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

        # Append or insert rule
        section_header = plan.get("section_header")
        if section_header and section_header in current_text:
            idx = current_text.find(section_header) + len(section_header)
            # Find next line
            next_newline = current_text.find("\n", idx)
            if next_newline != -1:
                updated_text = current_text[:next_newline + 1] + f"\n{rule_markdown}\n" + current_text[next_newline + 1:]
            else:
                updated_text = current_text + f"\n\n{rule_markdown}\n"
        else:
            updated_text = current_text.rstrip() + f"\n\n{rule_markdown}\n"

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
