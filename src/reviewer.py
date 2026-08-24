import json
import logging
from typing import List, Dict, Any, Optional
import requests
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from src.config import TommiConfig
from src.diff_parser import parse_unified_diff, ParsedDiff
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
        max_tool_turns = 10

        for turn in range(max_tool_turns):
            gen_config = types.GenerateContentConfig(
                temperature=0.15,
                max_output_tokens=65536,
            )
            if tools_list:
                gen_config.tools = tools_list

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

        return ""

    def review_pr(self, pr_title: str, pr_body: str, pr_url: str) -> List[Dict[str, Any]]:
        """
        Executes code review analysis on the pull request.
        """
        logger.info(f"Fetching PR #{self.config.pr_number} diff...")
        diff_text = self.fetch_pr_diff(pr_url)

        if not diff_text.strip():
            logger.info("PR diff is empty. Nothing to review.")
            return []

        parsed_diff = parse_unified_diff(diff_text)
        rules = load_all_rules()
        candidate_models = resolve_candidate_models(self.client, self.config.model_name)

        logger.info(f"Loaded rules ({len(rules.base_rules)} base modules, {len(rules.local_rules)} local files).")
        prompt = self._build_review_prompt(pr_title, pr_body, diff_text, rules, parsed_diff=parsed_diff)

        comments_data = None
        last_error = None
        for i, model_name in enumerate(candidate_models):
            logger.info(f"Running Gemini review with model '{model_name}'...")
            try:
                try:
                    raw_json = self._execute_review_generation(model_name, prompt, enable_tools=True)
                except Exception as tool_err:
                    logger.warning(f"Model '{model_name}' failed with tools ({tool_err}), falling back without tools...")
                    raw_json = self._execute_review_generation(model_name, prompt, enable_tools=False)

                comments_data = self._parse_and_repair_json(raw_json)
                break
            except Exception as e:
                error_str = str(e).lower()
                if "503" in error_str or "high demand" in error_str or "unavailable" in error_str or "overloaded" in error_str:
                    logger.warning(f"Model '{model_name}' is experiencing high demand (503).")
                    last_error = e
                    if i < len(candidate_models) - 1:
                        logger.info(f"Retrying with fallback model '{candidate_models[i+1]}'...")
                        continue
                    raise HighDemandException("T.O.M.M.I. is currently experiencing high demand. Please try again in a few moments.") from e
                elif "429" in error_str or "quota" in error_str or "exhausted" in error_str:
                    raise QuotaExceededException("T.O.M.M.I. has run out of AI API quota for today. Please try again later.") from e
                else:
                    logger.warning(f"Failed to generate or parse AI review response with model '{model_name}': {e}")
                    last_error = e
                    if i < len(candidate_models) - 1:
                        logger.info(f"Retrying with fallback model '{candidate_models[i+1]}'...")
                        continue
                    raise RuntimeError(f"Failed to generate or parse AI review response: {e}") from e

        if comments_data is None:
            if last_error:
                raise HighDemandException("T.O.M.M.I. is currently experiencing high demand. Please try again in a few moments.") from last_error
            raise RuntimeError("Failed to obtain response from Gemini API.")

        # Validate, adjust line numbers, and sort by severity priority
        validated_comments = self._validate_comments(comments_data, parsed_diff)
        return validated_comments

    def _build_review_prompt(self, pr_title: str, pr_body: str, diff_text: str, rules: LoadedRules, parsed_diff: Optional[ParsedDiff] = None) -> str:
        formatted_rules = rules.format_for_prompt()

        full_files_context = []
        if parsed_diff and parsed_diff.files:
            for file_path in list(parsed_diff.files.keys())[:10]:
                content = self.inspector.read_file(file_path, start_line=1, end_line=500)
                if not content.startswith("Error:"):
                    full_files_context.append(content)

        full_files_section = ""
        if full_files_context:
            full_files_section = "### MODIFIED FILES SURROUNDING SOURCE CODE (from checked-out repository):\n" + "\n\n".join(full_files_context) + "\n\n"

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

### REPOSITORY CODE TRACING INSTRUCTIONS & TOOLS:
You have full access to workspace inspection tools (`read_file`, `search_codebase`, `find_files`, `get_symbol_definition`) to search, trace, and read ANY file in the repository workspace.
1. **Trace Method Contracts Before Reviewing**: If code in the PR calls an external method, data attachment, helper, or class whose declaration is not in the diff, USE YOUR TOOLS to find and read the method's declaration and implementation.
2. **Never Guess Return Types & Contracts**: Do NOT assume a method returns null, is a simple getter, or does not instantiate data (e.g. `get()` methods often act as `getOrCreate` in Minecraft mods). Look up the method definition first!
3. **Verify Units**: Verify whether time values, cooldowns, or durations use ticks (via `SharedConstants.TICKS_PER_SECOND`) or other units in the referenced classes.

### REVIEW PRIORITIZATION & SEVERITY TRIAGE:
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

### INSTRUCTIONS:
1. Review the entire diff thoroughly and comprehensively. Do NOT artificially limit or truncate the number of comments—report ALL genuine violations, bugs, side-safety issues, performance regressions, and style breaches found across all modified files and hunks.
2. ALWAYS prioritize reporting critical bugs, side-safety crashes, and performance issues before reporting cosmetic style/naming nitpicks.
3. Be concise, direct, and instructional in your comments. Point out what is wrong and exactly how to fix it according to your rules.
4. Do NOT leave generic praise or comment on valid, unchanged code.
5. Return your comments as a strict JSON array of objects, ordered from highest priority/severity to lowest priority/severity (`CRITICAL` first, then `WARNING`, then `SUGGESTION`).
6. Each object must have:
   - `path`: The exact relative file path of the file being reviewed (matching the `b/` path in diff).
   - `line`: The exact line number in the NEW version of the file (RIGHT side of diff) where the issue occurs.
   - `severity`: One of `"CRITICAL"`, `"WARNING"`, or `"SUGGESTION"`.
   - `body`: Your review comment.
7. If there are no issues found, return an empty array `[]`.
8. Return ONLY the raw JSON array.
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

