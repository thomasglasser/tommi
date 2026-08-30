import json
import logging
import time
from typing import List, Dict, Any, Optional
import requests
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from src.config import TommiConfig
from src.diff_parser import parse_unified_diff, ParsedDiff, filter_diff_for_review
from src.rules_loader import load_all_rules, LoadedRules
from src.models_resolver import resolve_candidate_models, resolve_model_name
from src.repo_tools import WorkspaceInspector

logger = logging.getLogger("tommi.reviewer")


class ReviewCommentItem(BaseModel):
    path: str = Field(description="The exact relative file path of the file being reviewed (matching the b/ path in diff).")
    line: int = Field(description="The exact line number in the NEW version of the file (RIGHT side of diff) where the issue occurs.")
    severity: str = Field(default="WARNING", description="Severity of the issue: CRITICAL, WARNING, or SUGGESTION.")
    body: str = Field(description="The review comment explaining the issue and how to resolve it.")


class TommiReviewer:
    def __init__(self, config: TommiConfig, auth_token: Optional[str] = None, workspace_dir: Optional[str] = None):
        self.config = config
        self.auth_token = auth_token or config.github_token
        self.client = genai.Client(api_key=config.gemini_api_key)
        self.inspector = WorkspaceInspector(workspace_dir=workspace_dir)

    def fetch_pr_diff(self, pr_url: str) -> str:
        """Fetches the raw diff of the PR using GitHub API."""
        headers = {
            "Accept": "application/vnd.github.v3.diff",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        resp = requests.get(pr_url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch PR diff (HTTP {resp.status_code}): {resp.text}")
        return resp.text

    def _parse_and_repair_json(self, raw_text: str) -> List[Dict[str, Any]]:
        """
        Parses JSON response text from Gemini, handling markdown code fences,
        embedded JSON in prose, and gracefully salvaging truncated JSON arrays.
        """
        if not raw_text or not raw_text.strip():
            return []

        text = raw_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # 1. Direct JSON parse (with strict=False to handle unescaped control chars)
        try:
            data = json.loads(text, strict=False)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("comments", "reviews", "review_comments", "items", "data"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
                return [data]
        except Exception:
            pass

        # 2. Extract bracketed array substring if model wrapped it in prose
        start_idx = text.find("[")
        if start_idx != -1:
            end_idx = text.rfind("]")
            if end_idx != -1 and end_idx > start_idx:
                try:
                    data = json.loads(text[start_idx:end_idx + 1], strict=False)
                    if isinstance(data, list):
                        return data
                except Exception:
                    pass

        # 3. Attempt salvage of truncated JSON array (e.g. if token limit cut off the last item)
        if start_idx != -1:
            last_brace = text.rfind("}")
            if last_brace != -1 and last_brace > start_idx:
                salvage_candidate = text[start_idx:last_brace + 1].strip() + "]"
                try:
                    data = json.loads(salvage_candidate, strict=False)
                    if isinstance(data, list) and data:
                        logger.warning(
                            f"AI review JSON was truncated mid-generation. Successfully salvaged {len(data)} completed review comment(s)."
                        )
                        return data
                except Exception:
                    pass

        # 4. If all parsing/salvage attempts fail, raise RuntimeError
        raise RuntimeError(f"Unable to parse AI review JSON response: {text[:200]}...")

    def _execute_review_generation(
        self,
        model_name: str,
        prompt: str,
        enable_tools: bool = True
    ) -> str:
        """
        Executes review generation against Gemini, executing tool calls when Gemini needs
        to inspect workspace files or trace definitions.
        """
        tool_map = self.inspector.get_tool_callables()
        tools_list = list(tool_map.values()) if enable_tools else None

        contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
        max_tool_turns = 3

        for turn in range(max_tool_turns):
            gen_config = types.GenerateContentConfig(
                temperature=0.15,
                max_output_tokens=65536,
            )
            if tools_list:
                gen_config.tools = tools_list
                gen_config.automatic_function_calling = types.AutomaticFunctionCallingConfig(
                    disable=True
                )

            response = self.client.models.generate_content(
                model=model_name,
                contents=contents,
                config=gen_config,
            )

            # Check if Gemini returned function calls
            raw_fcs = getattr(response, "function_calls", None)
            function_calls = []
            if raw_fcs:
                try:
                    for fc in raw_fcs:
                        fc_name = getattr(fc, "name", None)
                        if isinstance(fc_name, str) and fc_name in tool_map:
                            function_calls.append(fc)
                except Exception:
                    pass

            if not function_calls and response and hasattr(response, "candidates") and response.candidates:
                candidate = response.candidates[0]
                if candidate and hasattr(candidate, "content") and candidate.content and hasattr(candidate.content, "parts"):
                    for p in (candidate.content.parts or []):
                        fc = getattr(p, "function_call", None)
                        fc_name = getattr(fc, "name", None)
                        if isinstance(fc_name, str) and fc_name in tool_map:
                            function_calls.append(fc)

            if not function_calls:
                # No more tool calls, return text
                return response.text.strip() if response and response.text else ""

            # Execute tool calls
            tool_response_parts = []
            for fc in function_calls:
                fn_name = getattr(fc, "name", "")
                fn_args = getattr(fc, "args", {}) or {}
                if isinstance(fn_args, dict):
                    call_kwargs = fn_args
                elif hasattr(fn_args, "items"):
                    call_kwargs = dict(fn_args.items())
                else:
                    call_kwargs = {}

                logger.info(f"T.O.M.M.I. workspace tool call: {fn_name}({call_kwargs})")
                tool_fn = tool_map.get(fn_name)
                if tool_fn:
                    try:
                        result = str(tool_fn(**call_kwargs))
                    except Exception as err:
                        result = f"Error executing {fn_name}: {err}"
                else:
                    result = f"Tool '{fn_name}' not found."

                tool_response_parts.append(
                    types.Part.from_function_response(
                        name=fn_name,
                        response={"result": result}
                    )
                )

            # Append model candidate and tool response to conversation history
            if response.candidates and response.candidates[0].content:
                contents.append(response.candidates[0].content)
            else:
                contents.append(types.Content(role="model", parts=[types.Part.from_function_call(name=fc.name, args=getattr(fc, "args", {})) for fc in function_calls]))

            contents.append(types.Content(role="user", parts=tool_response_parts))
            time.sleep(1)

        # If tool budget reached, make one final generation turn without tools to synthesize review
        logger.info("Tool budget reached. Requesting final review synthesis...")
        final_config = types.GenerateContentConfig(
            temperature=0.15,
            max_output_tokens=65536,
        )
        final_response = self.client.models.generate_content(
            model=model_name,
            contents=contents,
            config=final_config,
        )
        return final_response.text.strip() if final_response and final_response.text else ""

    def review_pr(self, pr_title: str, pr_body: str, pr_url: str, enable_tools: bool = False) -> List[Dict[str, Any]]:
        """
        Executes code review analysis on the pull request with transient error retry and model candidate failover.
        """
        logger.info(f"Fetching PR #{self.config.pr_number} diff...")
        diff_text = self.fetch_pr_diff(pr_url)

        filtered_diff = filter_diff_for_review(diff_text)
        if not filtered_diff.strip():
            logger.info("PR diff contains no reviewable code files. Nothing to review.")
            return []

        parsed_diff = parse_unified_diff(filtered_diff)
        rules = load_all_rules()
        candidate_models = resolve_candidate_models(self.client, self.config.model_name)

        logger.info(f"Loaded rules ({len(rules.base_rules)} base modules, {len(rules.local_rules)} local files).")
        prompt = self._build_review_prompt(pr_title, pr_body, filtered_diff, rules, parsed_diff=parsed_diff, enable_tools=enable_tools)

        comments_data = None
        last_error = None
        encountered_429 = False
        encountered_503 = False

        for i, model_name in enumerate(candidate_models):
            logger.info(f"Running Gemini review with model '{model_name}'...")
            model_succeeded = False
            max_attempts = 2

            for attempt in range(max_attempts):
                # Only use tools on attempt 0 if explicitly enabled; retry attempt always disables tools
                use_tools = enable_tools if attempt == 0 else False
                try:
                    raw_json = self._execute_review_generation(model_name, prompt, enable_tools=use_tools)
                    comments_data = self._parse_and_repair_json(raw_json)
                    model_succeeded = True
                    break
                except Exception as e:
                    error_str = str(e).lower()
                    last_error = e
                    is_503 = "503" in error_str or "high demand" in error_str or "unavailable" in error_str or "overloaded" in error_str
                    is_429 = "429" in error_str or "quota" in error_str or "exhausted" in error_str or "resourceexhausted" in error_str or "rate limit" in error_str or "too many requests" in error_str

                    if is_503:
                        encountered_503 = True
                    if is_429:
                        encountered_429 = True

                    if is_503 or is_429:
                        if attempt < max_attempts - 1:
                            backoff_sec = (attempt + 1) * 5
                            logger.warning(
                                f"Model '{model_name}' encountered {'high demand (503)' if is_503 else 'rate limit (429)'} on attempt {attempt + 1}. "
                                f"Backing off for {backoff_sec}s before retrying..."
                            )
                            time.sleep(backoff_sec)
                            continue
                        else:
                            logger.warning(f"Model '{model_name}' exhausted retries on {'503 high demand' if is_503 else '429 rate limit'}.")
                            break
                    else:
                        logger.warning(f"Generation or JSON parsing failed with model '{model_name}': {e}")
                        if attempt < max_attempts - 1:
                            time.sleep(2)
                            continue
                        break

            if model_succeeded and comments_data is not None:
                break
            elif i < len(candidate_models) - 1:
                if encountered_429 or encountered_503:
                    time.sleep(3)
                logger.info(f"Failing over to next candidate model '{candidate_models[i + 1]}'...")

        if comments_data is None:
            if encountered_429:
                raise QuotaExceededException("T.O.M.M.I. has run out of AI API quota / rate limit. Please try again later.")
            elif encountered_503:
                raise HighDemandException("T.O.M.M.I. is currently experiencing high demand. Please try again in a few moments.")
            elif last_error:
                raise RuntimeError(f"Failed to generate or parse AI review response: {last_error}") from last_error
            raise RuntimeError("Failed to obtain response from Gemini API.")

        # Validate, adjust line numbers, and sort by severity priority
        validated_comments = self._validate_comments(comments_data, parsed_diff)
        return validated_comments

    def _build_review_prompt(
        self,
        pr_title: str,
        pr_body: str,
        diff_text: str,
        rules: LoadedRules,
        parsed_diff: Optional[ParsedDiff] = None,
        enable_tools: bool = False
    ) -> str:
        formatted_rules = rules.format_for_prompt()

        full_files_context = []
        if parsed_diff and parsed_diff.files:
            for file_path, lines_set in list(parsed_diff.files.items())[:15]:
                content = self.inspector.get_hunk_context(file_path, changed_lines=list(lines_set), padding=40)
                if not content.startswith("Error:"):
                    full_files_context.append(content)

        full_files_section = ""
        if full_files_context:
            full_files_section = "### MODIFIED FILES SURROUNDING SOURCE CODE (from checked-out repository):\n" + "\n\n".join(full_files_context) + "\n\n"

        tools_section = ""
        if enable_tools:
            tools_section = """### REPOSITORY CODE TRACING INSTRUCTIONS & TOOLS:
You have access to workspace inspection tools (`read_file`, `search_codebase`, `find_files`, `get_symbol_definition`) to search, trace, and read files in the repository workspace.
- **Surrounding Context Already Provided**: The surrounding source code for all modified files is already provided above in the 'MODIFIED FILES SURROUNDING SOURCE CODE' section. Do NOT make redundant tool calls to re-read files already shown above.
- **Trace External Contracts**: If code in the PR calls external methods, data attachments, helpers, or classes across the codebase whose declarations are not in the diff or surrounding code, use tools (`get_symbol_definition` or `search_codebase`) to check their definitions.
- **Never Guess Return Types & Contracts**: Do NOT assume a method returns null, is a simple getter, or does not instantiate data (e.g. `get()` methods often act as `getOrCreate` in Minecraft mods). Look up the method definition first!
- **Verify Units**: Verify whether time values, cooldowns, or durations use ticks (via `SharedConstants.TICKS_PER_SECOND`) or other units in the referenced classes.

"""

        return f"""
You are Thomas Glasser (@thomasglasser), an expert Minecraft/NeoForge mod developer, architect, and strict code reviewer.
You are reviewing a Pull Request in one of your repositories.

### YOUR CODE STANDARDS & EXPECTATIONS:
{formatted_rules}

### PULL REQUEST INFORMATION:
- **Title**: {pr_title}
- **Description**: {pr_body or '(No description provided)'}

{full_files_section}### PULL REQUEST DIFF:
```diff
{diff_text}
```

{tools_section}### REVIEW PRIORITIZATION & SEVERITY TRIAGE:
Evaluate every file and changed line thoroughly across the entire diff. Prioritize issues according to this hierarchy:
1. **CRITICAL**:
   - Functional bugs, logic flaws, broken math, or incorrect state mutations.
   - Client/Server side safety violations (e.g. referencing client-only classes from common code, dedicated server crashes).
   - Severe performance regressions (e.g. object allocations in `tick()` or per-frame methods, missing `BlockPos.Mutable`, unthrottled loops).
   - Concurrency bugs, race conditions, or state corruption.
2. **WARNING**:
   - Architectural and contract violations (e.g. missing `Holder<T>` wrappers, hardcoded blocks/items instead of tags, improper lifecycle cleanup).
   - Improper API / collection usage (e.g. standard `ArrayList` instead of FastUtil, streams in hot paths).
   - Missing null checks or safety guards where nullability is ambiguous.
3. **SUGGESTION**:
   - Minor code style, naming conventions (abbreviations, non-descriptive variable names), class layout ordering, dead code, single-use variables needing inlining, or javadoc formatting.

### INSTRUCTIONS & SUGGESTION FORMAT:
1. Review the entire diff thoroughly and comprehensively. Do NOT artificially limit or truncate the number of comments—report ALL genuine violations, bugs, side-safety issues, performance regressions, and style breaches found across all modified files and hunks.
2. ALWAYS prioritize reporting critical bugs, side-safety crashes, and performance issues before reporting cosmetic style/naming nitpicks.
3. Be concise, direct, and instructional in your comments. Point out what is wrong and exactly how to fix it according to your rules.
4. **1-Click GitHub Suggestions**: When suggesting an exact code replacement for a specific line, format the replacement inside a GitHub markdown suggestion block:
   ```suggestion
   exact replacement code
   ```
5. Do NOT leave generic praise or comment on valid, unchanged code.
6. Return your comments as a strict JSON array of objects, ordered from highest priority/severity to lowest priority/severity (`CRITICAL` first, then `WARNING`, then `SUGGESTION`).
7. Each object must have:
   - `path`: The exact relative file path of the file being reviewed (matching the `b/` path in diff).
   - `line`: The exact line number in the NEW version of the file (RIGHT side of diff) where the issue occurs.
   - `severity`: One of `"CRITICAL"`, `"WARNING"`, or `"SUGGESTION"`.
   - `body`: Your review comment.
8. If there are no issues found, return an empty array `[]`.
9. Return ONLY the raw JSON array.
"""

    def _validate_comments(self, raw_comments: List[Dict[str, Any]], parsed_diff: ParsedDiff) -> List[Dict[str, Any]]:
        severity_rank = {
            "CRITICAL": 1,
            "WARNING": 2,
            "SUGGESTION": 3,
        }

        validated = []
        for item in raw_comments:
            path = item.get("path")
            line = item.get("line")
            body = item.get("body")
            raw_sev = str(item.get("severity", "WARNING")).strip().upper()
            severity = raw_sev if raw_sev in severity_rank else "WARNING"

            if not path or not line or not body:
                continue

            # Ensure line number is an int
            try:
                line = int(line)
            except (ValueError, TypeError):
                continue

            # If line is in diff, keep as is. Otherwise find closest line in diff hunk.
            if not parsed_diff.is_line_in_diff(path, line):
                closest = parsed_diff.get_closest_valid_line(path, line)
                if closest is not None:
                    line = closest

            validated.append({
                "path": path,
                "line": line,
                "body": body,
                "severity": severity,
            })

        # Stable sort by severity: CRITICAL -> WARNING -> SUGGESTION
        validated.sort(key=lambda c: severity_rank.get(c.get("severity", "WARNING"), 99))
        return validated


class QuotaExceededException(Exception):
    pass


class HighDemandException(Exception):
    pass

