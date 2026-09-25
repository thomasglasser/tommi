import json
import logging
import os
import re
import time
from typing import List, Dict, Any, Optional, Tuple
import requests
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from src.config import TommiConfig
from src.diff_parser import parse_unified_diff, ParsedDiff, filter_diff_for_review, format_annotated_diff, split_diff_into_batches
from src.rules_loader import load_all_rules, LoadedRules
from src.models_resolver import resolve_candidate_models, resolve_model_name
from src.repo_tools import WorkspaceInspector

logger = logging.getLogger("tommi.reviewer")


class ReviewCommentItem(BaseModel):
    path: str = Field(description="The exact relative file path of the file being reviewed (matching the b/ path in diff).")
    line: int = Field(description="The exact line number in the NEW version of the file (RIGHT side of diff) where the issue occurs. Read this directly from the line prefix in the annotated diff.")
    severity: str = Field(default="WARNING", description="Severity of the issue: CRITICAL, WARNING, or SUGGESTION.")
    target_code: Optional[str] = Field(default=None, description="The exact single line of code or distinctive snippet from the diff being targeted.")
    body: str = Field(description="The review comment explaining the issue and how to resolve it.")
    actionable: bool = Field(default=True, description="Set to false if while reviewing or drafting you realize there is no genuine issue or that the code is intentional/valid.")


def extract_retry_delay(error: Exception) -> Optional[float]:
    """
    Extracts recommended retryDelay (in seconds) from a Google GenAI / API error if present.
    Checks structured error details and regex matches in string representation.
    """
    try:
        for attr in ("details", "error", "args"):
            val = getattr(error, attr, None)
            candidates_to_check = [val] if not isinstance(val, (list, tuple)) else list(val)
            for item in candidates_to_check:
                if isinstance(item, dict):
                    details = item.get("details", [])
                    if isinstance(details, list):
                        for d in details:
                            if isinstance(d, dict) and "retryDelay" in d:
                                delay_str = str(d["retryDelay"]).rstrip("s")
                                return float(delay_str)
                    if "retryDelay" in item:
                        delay_str = str(item["retryDelay"]).rstrip("s")
                        return float(delay_str)
    except Exception:
        pass

    err_text = str(error)
    m = re.search(r"['\"]?retryDelay['\"]?\s*:\s*['\"]?([\d\.]+)s?['\"]?", err_text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    m2 = re.search(r"retry (?:in|after) ([\d\.]+)\s*s", err_text, re.IGNORECASE)
    if m2:
        try:
            return float(m2.group(1))
        except ValueError:
            pass

    return None


class TommiReviewer:
    def __init__(self, config: TommiConfig, auth_token: Optional[str] = None, workspace_dir: Optional[str] = None):
        self.config = config
        self.auth_token = auth_token or config.github_token
        self.client = genai.Client(api_key=config.gemini_api_key)
        self.inspector = WorkspaceInspector(workspace_dir=workspace_dir)
        self.unreviewed_files: List[str] = []

    def fetch_pr_diff(self, pr_url: str) -> str:
        """
        Fetches the raw unified diff of the PR using GitHub API.

        Falls back to assembling the diff from the paginated /pulls/{n}/files endpoint
        when GitHub returns HTTP 406 (diff too large, exceeds 20 000 lines).
        """
        headers = {
            "Accept": "application/vnd.github.v3.diff",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        resp = requests.get(pr_url, headers=headers)
        if resp.status_code == 200:
            return resp.text

        if resp.status_code == 406:
            # Diff too large for the single-endpoint response — assemble from file patches.
            logger.warning(
                f"PR diff endpoint returned 406 (diff too large). "
                f"Falling back to paginated /files endpoint to assemble diff..."
            )
            return self._fetch_pr_diff_from_files(pr_url)

        raise RuntimeError(f"Failed to fetch PR diff (HTTP {resp.status_code}): {resp.text}")

    def _fetch_pr_diff_from_files(self, pr_url: str) -> str:
        """
        Assembles a unified-diff-compatible string by collecting the `patch` field
        from each file returned by the paginated GET /pulls/{n}/files endpoint.

        GitHub paginates at 30 files per page (max 100 with ?per_page=100).
        Files without a `patch` (e.g. binary files or files too large to patch) are skipped.
        """
        json_headers = {
            "Accept": "application/vnd.github.v3+json",
        }
        if self.auth_token:
            json_headers["Authorization"] = f"Bearer {self.auth_token}"

        # The files endpoint is at the same base URL + /files
        files_url = pr_url.rstrip("/") + "/files"
        diff_chunks: List[str] = []
        page = 1

        while True:
            resp = requests.get(files_url, headers=json_headers, params={"per_page": 100, "page": page})
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Failed to fetch PR files page {page} (HTTP {resp.status_code}): {resp.text}"
                )

            file_entries = resp.json()
            if not file_entries:
                break  # No more pages

            for entry in file_entries:
                filename = entry.get("filename", "")
                previous_filename = entry.get("previous_filename", filename)
                patch = entry.get("patch")  # May be absent for binary / oversized files
                status = entry.get("status", "modified")  # added, removed, modified, renamed

                if not patch:
                    # Binary or per-file-too-large — no reviewable content
                    continue

                # Reconstruct a minimal diff header so parse_unified_diff can handle it
                if status == "renamed":
                    header = f"diff --git a/{previous_filename} b/{filename}\n"
                    header += f"--- a/{previous_filename}\n"
                else:
                    header = f"diff --git a/{filename} b/{filename}\n"
                    if status == "added":
                        header += f"--- /dev/null\n"
                    else:
                        header += f"--- a/{filename}\n"

                header += f"+++ b/{filename}\n"
                diff_chunks.append(header + patch)

            # If fewer than 100 results came back, we've hit the last page
            if len(file_entries) < 100:
                break

            page += 1

        assembled = "\n".join(diff_chunks)
        logger.info(
            f"Assembled diff from {len(diff_chunks)} file patch(es) via paginated /files endpoint "
            f"({len(assembled)} chars total)."
        )
        return assembled


    def _parse_and_repair_json(self, raw_text: str) -> List[Dict[str, Any]]:
        """
        Parses JSON response text from Gemini, handling markdown code fences,
        embedded JSON in prose, and gracefully salvaging truncated JSON arrays.
        """
        if not raw_text or not raw_text.strip():
            return []

        text = raw_text.strip()

        def _extract_comments(data: Any) -> Optional[List[Dict[str, Any]]]:
            if isinstance(data, list):
                if not data or all(isinstance(item, dict) for item in data):
                    return data
            elif isinstance(data, dict):
                for key in ("comments", "reviews", "review_comments", "items", "data"):
                    val = data.get(key)
                    if isinstance(val, list) and (not val or all(isinstance(item, dict) for item in val)):
                        return val
                if "path" in data and "line" in data:
                    return [data]
                for val in data.values():
                    if isinstance(val, list) and val and all(isinstance(item, dict) for item in val):
                        return val
                return [data]
            return None

        def _try_parse(candidate: str) -> Optional[List[Dict[str, Any]]]:
            if not candidate or not candidate.strip():
                return None
            try:
                return _extract_comments(json.loads(candidate.strip(), strict=False))
            except Exception:
                return None

        # 1. Direct JSON parse
        parsed = _try_parse(text)
        if parsed is not None:
            return parsed

        # 2. Extract from markdown code fences ```json ... ``` or ``` ... ```
        for match in re.finditer(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.DOTALL):
            parsed = _try_parse(match.group(1))
            if parsed is not None:
                return parsed

        # 3. Strip outer markdown fences if present
        clean_text = text
        if "```json" in clean_text:
            clean_text = clean_text.split("```json", 1)[1]
        elif "```" in clean_text:
            clean_text = clean_text.split("```", 1)[1]
        if "```" in clean_text:
            clean_text = clean_text.rsplit("```", 1)[0]
        clean_text = clean_text.strip()

        candidates = [clean_text, text] if clean_text != text else [text]
        decoder = json.JSONDecoder(strict=False)

        # 4. Use raw_decode at every potential JSON array start
        for cand in candidates:
            array_starts = [m.start() for m in re.finditer(r"\[\s*(?:\{|\])", cand)]
            if not array_starts and "[" in cand:
                array_starts = [cand.find("[")]

            for start_idx in array_starts:
                try:
                    obj, _ = decoder.raw_decode(cand, idx=start_idx)
                    extracted = _extract_comments(obj)
                    if extracted is not None:
                        return extracted
                except Exception:
                    pass

        # 5. Use raw_decode at every potential JSON object start
        for cand in candidates:
            object_starts = [m.start() for m in re.finditer(r"\{\s*\"(?:comments|reviews|review_comments|items|data|path)\"", cand)]
            if not object_starts and "{" in cand:
                object_starts = [cand.find("{")]

            for start_idx in object_starts:
                try:
                    obj, _ = decoder.raw_decode(cand, idx=start_idx)
                    extracted = _extract_comments(obj)
                    if extracted is not None:
                        return extracted
                except Exception:
                    pass

        # 6. Fallback bracket matching: test closing brackets after start_idx in reverse
        for cand in candidates:
            array_starts = [m.start() for m in re.finditer(r"\[\s*(?:\{|\])", cand)]
            if not array_starts and "[" in cand:
                array_starts = [cand.find("[")]

            for start_idx in array_starts:
                bracket_positions = [i for i, char in enumerate(cand) if char == ']' and i > start_idx]
                for end_idx in reversed(bracket_positions):
                    parsed = _try_parse(cand[start_idx:end_idx + 1])
                    if parsed is not None:
                        return parsed

        # 7. Salvage truncated JSON array (e.g. if token limit cut off the last item)
        for cand in candidates:
            array_starts = [m.start() for m in re.finditer(r"\[\s*\{", cand)]
            if not array_starts and "[" in cand:
                array_starts = [cand.find("[")]

            for start_idx in array_starts:
                brace_positions = [i for i, char in enumerate(cand) if char == '}' and i > start_idx]
                for last_brace in reversed(brace_positions):
                    salvaged = _try_parse(cand[start_idx:last_brace + 1].strip() + "]")
                    if salvaged:
                        logger.warning(
                            f"AI review JSON was truncated mid-generation. Successfully salvaged {len(salvaged)} completed review comment(s)."
                        )
                        return salvaged

        # 8. If all parsing/salvage attempts fail, log preview and raise RuntimeError
        logger.warning(f"Unparseable AI response text (first 2000 chars):\n{text[:2000]}")
        raise RuntimeError(f"Unable to parse AI review JSON response: {text[:200]}...")

    def _extract_response_text(self, response: Any) -> str:
        """
        Extracts review output text from Gemini response, prioritizing non-thought text parts
        and checking for token exhaustion.
        """
        if not response:
            return ""

        candidate = response.candidates[0] if (hasattr(response, "candidates") and response.candidates) else None
        if not candidate:
            return response.text.strip() if hasattr(response, "text") and response.text else ""

        finish_reason = getattr(candidate, "finish_reason", None)
        if finish_reason and "MAX_TOKENS" in str(finish_reason).upper():
            logger.warning("Gemini generation hit MAX_TOKENS limit; output may be truncated.")

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
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )
            if getattr(self.config, "thinking_budget", 0) > 0:
                gen_config.thinking_config = types.ThinkingConfig(
                    thinking_budget=self.config.thinking_budget
                )

            if tools_list:
                gen_config.tools = tools_list
            else:
                gen_config.response_mime_type = "application/json"
                gen_config.response_schema = list[ReviewCommentItem]

            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=gen_config,
                )
            except Exception as gen_err:
                response = None
                err_str = str(gen_err).lower()
                if getattr(gen_config, "thinking_config", None) and ("thinking" in err_str or "thought" in err_str):
                    logger.info(f"Model '{model_name}' does not support thinking_config. Retrying without thinking_config...")
                    gen_config.thinking_config = None
                    try:
                        response = self.client.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=gen_config,
                        )
                    except Exception as thinking_retry_err:
                        gen_err = thinking_retry_err
                        err_str = str(gen_err).lower()

                if response is None and gen_config.response_schema and ("schema" in err_str or "unsupported" in err_str):
                    logger.info(f"Model '{model_name}' does not support response_schema. Retrying without response_schema...")
                    gen_config.response_schema = None
                    try:
                        response = self.client.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=gen_config,
                        )
                    except Exception as schema_retry_err:
                        gen_err = schema_retry_err

                if response is None:
                    raise gen_err

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
                return self._extract_response_text(response)

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
            response_mime_type="application/json",
            response_schema=list[ReviewCommentItem],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if getattr(self.config, "thinking_budget", 0) > 0:
            final_config.thinking_config = types.ThinkingConfig(
                thinking_budget=self.config.thinking_budget
            )

        try:
            final_response = self.client.models.generate_content(
                model=model_name,
                contents=contents,
                config=final_config,
            )
        except Exception as gen_err:
            final_response = None
            err_str = str(gen_err).lower()
            if getattr(final_config, "thinking_config", None) and ("thinking" in err_str or "thought" in err_str):
                logger.info(f"Model '{model_name}' does not support thinking_config on final turn. Retrying without thinking_config...")
                final_config.thinking_config = None
                try:
                    final_response = self.client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=final_config,
                    )
                except Exception as final_thinking_retry_err:
                    gen_err = final_thinking_retry_err
                    err_str = str(gen_err).lower()

            if final_response is None and final_config.response_schema and ("schema" in err_str or "unsupported" in err_str):
                logger.info(f"Model '{model_name}' does not support response_schema on final turn. Retrying without response_schema...")
                final_config.response_schema = None
                try:
                    final_response = self.client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=final_config,
                    )
                except Exception as final_schema_retry_err:
                    gen_err = final_schema_retry_err

            if final_response is None:
                raise gen_err
        return self._extract_response_text(final_response)

    def _review_batch(
        self,
        batch_diff: str,
        batch_parsed_diff: ParsedDiff,
        b_idx: int,
        total_batches: int,
        pr_title: str,
        pr_body: str,
        rules: LoadedRules,
        candidate_models: List[str],
        model_cooldowns: Dict[str, float],
        preferred_model: Optional[str],
        enable_tools: bool = False,
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str], bool, bool, Optional[Exception]]:
        batch_annotated_diff = format_annotated_diff(batch_diff)
        batch_info = (b_idx + 1, total_batches) if total_batches > 1 else None
        batch_prompt = self._build_review_prompt(
            pr_title,
            pr_body,
            batch_annotated_diff,
            rules,
            parsed_diff=batch_parsed_diff,
            enable_tools=enable_tools,
            batch_info=batch_info,
        )

        # Organize candidate models respecting active cooldowns
        now = time.time()
        available_models = [m for m in candidate_models if model_cooldowns.get(m, 0) <= now]
        cooling_models = [m for m in candidate_models if model_cooldowns.get(m, 0) > now]
        cooling_models.sort(key=lambda m: model_cooldowns[m])

        if preferred_model and preferred_model in available_models:
            available_models.remove(preferred_model)
            available_models.insert(0, preferred_model)

        # If all models are cooling down, wait for the earliest one if within 45s
        if not available_models and cooling_models:
            earliest_model = cooling_models[0]
            wait_sec = model_cooldowns[earliest_model] - now
            if 0 < wait_sec <= 45:
                logger.info(
                    f"All candidate models are on cooldown. Waiting {wait_sec:.1f}s for '{earliest_model}' to cool down..."
                )
                time.sleep(wait_sec + 1)
                available_models.append(earliest_model)
                cooling_models = cooling_models[1:]

        models_to_try = available_models + cooling_models
        batch_comments = None
        succeeded_model = None
        encountered_429 = False
        encountered_503 = False
        last_error = None

        for i, model_name in enumerate(models_to_try):
            logger.info(f"Running Gemini review with model '{model_name}'...")
            model_succeeded = False
            max_attempts = 2

            for attempt in range(max_attempts):
                # Only use tools on attempt 0 if explicitly enabled; retry attempt always disables tools
                use_tools = enable_tools if attempt == 0 else False
                try:
                    raw_json = self._execute_review_generation(model_name, batch_prompt, enable_tools=use_tools)
                    batch_comments = self._parse_and_repair_json(raw_json)
                    model_succeeded = True
                    succeeded_model = model_name
                    model_cooldowns.pop(model_name, None)
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
                        retry_delay = extract_retry_delay(e)
                        if retry_delay is not None and retry_delay <= 15:
                            backoff_sec = retry_delay + 1
                            if attempt < max_attempts - 1:
                                logger.warning(
                                    f"Model '{model_name}' encountered {'high demand (503)' if is_503 else 'rate limit (429)'}: {e}. "
                                    f"Backing off for {backoff_sec:.1f}s before retrying..."
                                )
                                time.sleep(backoff_sec)
                                continue
                            else:
                                model_cooldowns[model_name] = time.time() + 60
                                logger.warning(f"Model '{model_name}' exhausted retries on {'503 high demand' if is_503 else '429 rate limit'}: {e}")
                                break
                        elif retry_delay is not None and retry_delay > 15:
                            model_cooldowns[model_name] = time.time() + retry_delay
                            logger.warning(
                                f"Model '{model_name}' encountered {'high demand (503)' if is_503 else 'rate limit (429)'}: {e}. "
                                f"Recommended retryDelay of {retry_delay:.1f}s exceeds short backoff. Marking model on cooldown and failing over immediately..."
                            )
                            break
                        else:
                            if attempt < max_attempts - 1:
                                backoff_sec = 5
                                logger.warning(
                                    f"Model '{model_name}' encountered {'high demand (503)' if is_503 else 'rate limit (429)'}: {e}. "
                                    f"Backing off for {backoff_sec}s before retrying..."
                                )
                                time.sleep(backoff_sec)
                                continue
                            else:
                                model_cooldowns[model_name] = time.time() + 60
                                logger.warning(f"Model '{model_name}' exhausted retries on {'503 high demand' if is_503 else '429 rate limit'}: {e}")
                                break
                    else:
                        logger.warning(f"Generation or JSON parsing failed with model '{model_name}': {e}")
                        if attempt < max_attempts - 1:
                            time.sleep(1)
                            continue
                        break

            if model_succeeded and batch_comments is not None:
                break
            elif i < len(models_to_try) - 1:
                if encountered_429 or encountered_503:
                    time.sleep(1)
                logger.info(f"Failing over to next candidate model '{models_to_try[i + 1]}'...")

        return batch_comments, succeeded_model, encountered_429, encountered_503, last_error

    def review_pr(self, pr_title: str, pr_body: str, pr_url: str, enable_tools: bool = False) -> List[Dict[str, Any]]:
        """
        Executes code review analysis on the pull request with transient error retry,
        automatic diff batching for large PRs, model candidate failover, and a second retry pass for any failed batches.
        """
        logger.info(f"Fetching PR #{self.config.pr_number} diff...")
        diff_text = self.fetch_pr_diff(pr_url)
        return self.review_diff(
            diff_text=diff_text,
            title=pr_title,
            description=pr_body,
            enable_tools=enable_tools,
        )

    def review_diff(
        self,
        diff_text: str,
        title: str = "Code Review",
        description: str = "",
        enable_tools: bool = False,
        repo_workspace_dir: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Executes code review analysis on unified diff text with transient error retry,
        automatic diff batching for large diffs, model candidate failover, and a second retry pass.
        """
        filtered_diff = filter_diff_for_review(diff_text)
        if not filtered_diff.strip():
            logger.info("Diff contains no reviewable code files. Nothing to review.")
            self.unreviewed_files = []
            return []

        parsed_diff = parse_unified_diff(filtered_diff)
        target_ws = repo_workspace_dir or getattr(self.inspector, "workspace_dir", None)
        rules = load_all_rules(repo_workspace_dir=target_ws)
        candidate_models = resolve_candidate_models(self.client, self.config.model_name)

        diff_batches = split_diff_into_batches(filtered_diff, max_files_per_batch=15, max_chars_per_batch=100_000)
        logger.info(f"Loaded rules ({len(rules.base_rules)} base modules, {len(rules.local_rules)} local files).")
        if len(diff_batches) > 1:
            logger.info(f"Diff is large ({len(parsed_diff.files)} files, {len(filtered_diff)} chars). Split into {len(diff_batches)} review batches to maintain high attention and prevent quota exhaustion.")

        all_comments_data = []
        preferred_model = None
        self.unreviewed_files = []
        successful_batches_count = 0
        model_cooldowns: Dict[str, float] = {}
        last_encountered_429 = False
        last_encountered_503 = False
        last_error = None
        failed_batches: List[Tuple[int, str, ParsedDiff]] = []

        consecutive_all_model_failures = 0

        # === PASS 1: Sequential review across all diff batches ===
        for b_idx, batch_diff in enumerate(diff_batches):
            if len(diff_batches) > 1:
                logger.info(f"--- Reviewing PR diff batch {b_idx + 1}/{len(diff_batches)} ({len(batch_diff)} chars) ---")

            batch_parsed_diff = parse_unified_diff(batch_diff)
            comments, succ_model, is_429, is_503, err = self._review_batch(
                batch_diff=batch_diff,
                batch_parsed_diff=batch_parsed_diff,
                b_idx=b_idx,
                total_batches=len(diff_batches),
                pr_title=title,
                pr_body=description,
                rules=rules,
                candidate_models=candidate_models,
                model_cooldowns=model_cooldowns,
                preferred_model=preferred_model,
                enable_tools=enable_tools,
            )

            if is_429:
                last_encountered_429 = True
            if is_503:
                last_encountered_503 = True
            if err:
                last_error = err

            if comments is not None:
                successful_batches_count += 1
                preferred_model = succ_model
                all_comments_data.extend(comments)
                consecutive_all_model_failures = 0
            else:
                consecutive_all_model_failures += 1

                # Early exit if the very first batch fails across all candidate models:
                # The API is completely unavailable or quota is exhausted before starting.
                if b_idx == 0:
                    logger.warning(
                        "Batch 1 failed across all candidate models. "
                        "Aborting review immediately to prevent long timeouts and quota exhaustion."
                    )
                    if is_429 or last_encountered_429:
                        raise QuotaExceededException("T.O.M.M.I. has run out of AI API quota / rate limit. Please try again later.")
                    elif is_503 or last_encountered_503:
                        raise HighDemandException("T.O.M.M.I. is currently experiencing high demand. Please try again in a few moments.")
                    elif err or last_error:
                        raise RuntimeError(f"Failed to generate or parse AI review response on Batch 1: {err or last_error}")
                    raise RuntimeError("Failed to obtain response from Gemini API on Batch 1.")

                failed_batches.append((b_idx, batch_diff, batch_parsed_diff))

                # If 2 consecutive batches fail across all candidate models mid-review,
                # the API is experiencing a persistent outage. Stop Pass 1 and return partial findings.
                if consecutive_all_model_failures >= 2 and successful_batches_count > 0:
                    logger.warning(
                        f"Encountered {consecutive_all_model_failures} consecutive batch failures across all candidate models. "
                        f"Aborting remaining batches early to return partial review findings."
                    )
                    for _, _, f_parsed in failed_batches:
                        self.unreviewed_files.extend(list(f_parsed.files.keys()))
                    failed_batches.clear()
                    for rem_idx in range(b_idx + 1, len(diff_batches)):
                        rem_diff = diff_batches[rem_idx]
                        rem_parsed = parse_unified_diff(rem_diff)
                        self.unreviewed_files.extend(list(rem_parsed.files.keys()))
                    break

            if b_idx < len(diff_batches) - 1:
                time.sleep(3)

        # === PASS 2: Rerun any failed batches after cooldown ===
        if failed_batches and successful_batches_count > 0 and consecutive_all_model_failures < 2:
            logger.info(
                f"Pass 1 completed with {len(failed_batches)} failed batch(es). "
                f"Attempting second pass to achieve full review coverage..."
            )
            # Check if any model cooldowns are active; wait if the earliest will expire within 30s
            now = time.time()
            cooling = [model_cooldowns[m] for m in candidate_models if model_cooldowns.get(m, 0) > now]
            if cooling:
                min_wait = min(cooling) - now
                if 0 < min_wait <= 30:
                    logger.info(f"Waiting {min_wait:.1f}s for candidate model cooldown to expire before retry pass...")
                    time.sleep(min_wait + 1)
                else:
                    time.sleep(3)
            else:
                time.sleep(3)

            for p2_i, (b_idx, batch_diff, batch_parsed_diff) in enumerate(failed_batches):
                logger.info(f"--- Retrying failed PR diff batch {b_idx + 1}/{len(diff_batches)} (Pass 2) ---")
                comments, succ_model, is_429, is_503, err = self._review_batch(
                    batch_diff=batch_diff,
                    batch_parsed_diff=batch_parsed_diff,
                    b_idx=b_idx,
                    total_batches=len(diff_batches),
                    pr_title=title,
                    pr_body=description,
                    rules=rules,
                    candidate_models=candidate_models,
                    model_cooldowns=model_cooldowns,
                    preferred_model=preferred_model,
                    enable_tools=False,
                )

                if is_429:
                    last_encountered_429 = True
                if is_503:
                    last_encountered_503 = True
                if err:
                    last_error = err

                if comments is not None:
                    successful_batches_count += 1
                    preferred_model = succ_model
                    all_comments_data.extend(comments)
                    logger.info(f"Retry pass succeeded for batch {b_idx + 1}! Recovered {len(comments)} comment(s).")
                else:
                    batch_files = list(batch_parsed_diff.files.keys())
                    logger.warning(
                        f"Batch {b_idx + 1}/{len(diff_batches)} failed on retry pass ({len(batch_files)} files skipped: {batch_files})."
                    )
                    self.unreviewed_files.extend(batch_files)

                    # Circuit Breaker on Pass 2: If a batch fails on retry pass across all candidate models,
                    # mark remaining retry batches as unreviewed and break immediately to avoid repeated 30-50s cooldowns.
                    for _, _, rem_p in failed_batches[p2_i + 1:]:
                        self.unreviewed_files.extend(list(rem_p.files.keys()))
                    logger.warning("Breaking out of Pass 2 early to prevent excessive timeouts on exhausted models.")
                    break

                time.sleep(2)

        # Deduplicate unreviewed file paths while preserving order
        self.unreviewed_files = list(dict.fromkeys(self.unreviewed_files))

        if successful_batches_count == 0:
            if last_encountered_429:
                raise QuotaExceededException("T.O.M.M.I. has run out of AI API quota / rate limit. Please try again later.")
            elif last_encountered_503:
                raise HighDemandException("T.O.M.M.I. is currently experiencing high demand. Please try again in a few moments.")
            elif last_error:
                raise RuntimeError(f"Failed to generate or parse AI review response: {last_error}") from last_error
            raise RuntimeError("Failed to obtain response from Gemini API.")

        # Validate, adjust line numbers, and sort by severity priority
        validated_comments = self._validate_comments(all_comments_data, parsed_diff)
        return validated_comments

    def _build_review_prompt(
        self,
        pr_title: str,
        pr_body: str,
        diff_text: str,
        rules: LoadedRules,
        parsed_diff: Optional[ParsedDiff] = None,
        enable_tools: bool = False,
        batch_info: Optional[tuple[int, int]] = None,
    ) -> str:
        formatted_rules = rules.format_for_prompt()

        full_files_context = []
        if parsed_diff and parsed_diff.files:
            for file_path, lines_set in list(parsed_diff.files.items())[:15]:
                content = self.inspector.get_hunk_context(file_path, changed_lines=list(lines_set), padding=100)
                if not content.startswith("Error:"):
                    full_files_context.append(content)

        full_files_section = ""
        if full_files_context:
            full_files_section = "### MODIFIED FILES SURROUNDING SOURCE CODE (from checked-out repository):\n" + "\n\n".join(full_files_context) + "\n\n"

        batch_header = ""
        if batch_info:
            curr_b, total_b = batch_info
            batch_header = f"\n- **Review Batch**: Part {curr_b} of {total_b} (focus specifically on the files in this batch diff)"

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
- **Description**: {pr_body or '(No description provided)'}{batch_header}

{full_files_section}### PULL REQUEST DIFF (Annotated with target line numbers on left):
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
   - Minor code style, naming conventions (abbreviations, non-descriptive variable names), American English spelling and grammar issues (in identifiers, comments, and Javadocs), class layout ordering, dead code, single-use variables needing inlining, or javadoc formatting.

### INSTRUCTIONS & SUGGESTION FORMAT:
1. Review the entire diff thoroughly and comprehensively. Do NOT artificially limit or truncate the number of comments—report ALL genuine violations, bugs, side-safety issues, performance regressions, and style breaches found across all modified files and hunks.
2. ALWAYS prioritize reporting critical bugs, side-safety crashes, and performance issues before reporting cosmetic style/naming nitpicks.
3. **Trust Compiler & Build Verification**: All PRs are verified to compile and build cleanly via Gradle prior to review. NEVER claim there are compilation errors, syntax errors, duplicate method/field definitions, or missing types that the Java compiler would reject. If you think a method is defined twice, you are misreading a method invocation (e.g. inside an `if` condition) or an overload. Do NOT flag compiler errors.
4. **Verify Full Method Scope for Variables**: NEVER report a parameter or variable as unused unless you have traced the entire method body and confirmed it is completely unreferenced. Check event postings (`NeoForge.EVENT_BUS.post(...)`), constructor arguments, method calls, lambda closures, and return values before alleging an unused parameter.
5. **Verify Full Class Scope for Methods & Fields**: Surrounding source code for all modified files is provided above in the 'MODIFIED FILES SURROUNDING SOURCE CODE' section. NEVER claim a method, field, helper, or override is unused, never called, or missing without checking the entire class. If a method is called by another method in the class, overrides an interface/parent method, acts as a factory, or listens to events (e.g. `@SubscribeEvent`), it is actively used.
6. **Avoid Before/After Inversion & Verify Member Order by Line Numbers**:
   When code is moved or refactored in a diff (deleted from one location and added in another), evaluate the code strictly in its NEW, final position (`+` lines) and check the 'MODIFIED FILES SURROUNDING SOURCE CODE'. NEVER instruct the author to perform a relocation or refactoring that the commit/PR has just performed (e.g. telling the author to move a constructor above static factories or move an instance method below static factories when the commit just moved them there). Check the actual line numbers in the surrounding code: if the constructor line number is lower than the static factory line number, the order is strictly correct. Do NOT report it!
7. **Parameter Shadowing & 'this.' Disambiguation**:
   In Java, ANY method parameter or local variable sharing a field's name strictly shadows that field, regardless of type. If a method parameter is named `holder`, accessing `this.holder` is MANDATORY to access the class field. NEVER claim `this.` is unnecessary or suggest removing it when a parameter or local variable has the same name, and NEVER suggest changes that produce self-referential calls (e.g. `holder.is(holder)` or `x.equals(x)`).
8. **Formatter Precedence & Empty Method Bodies**:
   Single-line empty method bodies (`{{}}`) such as `public static void init() {{}}`, no-op callbacks, or empty constructors are standard, clean, and enforced by automated repository formatters (Spotless / Immaculate). NEVER instruct authors to expand empty `{{}}` blocks across multiple lines, and NEVER flag empty initialization methods (`init()`, lifecycle hooks) as dead code.
9. **Service SPI Signatures & Covariant Registrars**:
   - NEVER suggest stripping parameters (such as `ResourceKey` registry keys) from low-level service interfaces (SPIs like `RegistrationService`, e.g. `createDataComponents`) to force consistency with other factory methods; low-level SPIs require full parameterization to support non-standard registries (e.g. custom component registries).
   - NEVER suggest composing or delegating from specialized registrars (`ItemsRegistrar`, `BlocksRegistrar`) to a base generic registrar (`FabricRegistrar<T>`); generic registrars return `ExtendedHolder<T, I>`, which breaks covariant return types (`ItemHolder<I>`, `BlockHolder<B>`) and forces redundant allocations and re-wrapping.
10. Be concise, direct, and instructional in your comments. Point out what is wrong and exactly how to fix it according to your rules.
11. **1-Click GitHub Suggestions**: When suggesting an exact code replacement for a specific line, format the replacement inside a GitHub markdown suggestion block:
   ```suggestion
   exact replacement code
   ```
12. **Self-Dismissal Protocol ("Changed Mind / No Issue")**:
   If while drafting a comment you realize there is actually no genuine issue (e.g. you notice parameter shadowing, intentional fallback, or that a rule does not apply):
   - Conclude the comment body with `[DISMISSED]` (e.g., `...So this is mandatory! [DISMISSED]`), or set `"actionable": false`.
   - The review engine will automatically recognize that you changed your mind and will discard the comment so it does not pollute the review!
   - If all candidate issues turn out to be non-issues, return an empty array `[]`.
13. Return your comments as a strict JSON array of objects, ordered from highest priority/severity to lowest priority/severity (`CRITICAL` first, then `WARNING`, then `SUGGESTION`).
14. Each object must have:
   - `path`: The exact relative file path of the file being reviewed (matching the `b/` path in diff).
   - `line`: The exact line number in the NEW version of the file (RIGHT side of diff) where the issue occurs. **CRITICAL**: Read the line number directly from the line prefix in the annotated diff (e.g. `  189: + ...` or `  190:   ...`). Do NOT count or estimate line numbers.
   - `target_code`: The exact line or distinctive snippet of code from the diff that this comment targets.
   - `severity`: One of `"CRITICAL"`, `"WARNING"`, or `"SUGGESTION"`.
   - `body`: Your review comment (or conclude with `[DISMISSED]` if you changed your mind).
   - `actionable`: Boolean (`true` by default, or `false` if dismissed as a non-issue).
15. If there are no issues found, return an empty array `[]`.
16. Return ONLY the raw JSON array starting with '[' and ending with ']'. Do NOT include conversational preamble, explanations, or markdown discussion outside the JSON.
"""

    def _align_suggestion_indentation(self, body: str, path: str, line: int, parsed_diff: ParsedDiff) -> str:
        """
        Ensures that code inside ```suggestion ... ``` matches the leading indentation
        of the target line in the diff.
        """
        target_indent = parsed_diff.get_line_indent(path, line)
        if not target_indent:
            return body

        pattern = re.compile(r"```suggestion\r?\n(.*?)\r?\n```", re.DOTALL)

        def _replace_block(match: re.Match) -> str:
            raw_code = match.group(1)
            lines = raw_code.splitlines()
            if not lines:
                return match.group(0)

            non_empty = [l for l in lines if l.strip()]
            if not non_empty:
                return match.group(0)

            min_sugg_indent = min(len(l) - len(l.lstrip()) for l in non_empty)
            if min_sugg_indent >= len(target_indent):
                return match.group(0)

            missing_indent = target_indent[min_sugg_indent:]
            indented_lines = [(missing_indent + l if l.strip() else "") for l in lines]
            return f"```suggestion\n{chr(10).join(indented_lines)}\n```"

        return pattern.sub(_replace_block, body)

    def _get_code_language(self, path: str) -> str:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        return {
            "java": "java",
            "py": "python",
            "kt": "kotlin",
            "ts": "typescript",
            "js": "javascript",
            "rs": "rust",
            "cpp": "cpp",
            "c": "c",
            "cs": "csharp",
            "go": "go",
            "json": "json",
            "gradle": "groovy",
        }.get(ext, "java")

    def _is_code_similar(self, a: str, b: str) -> bool:
        ca = a.strip()
        cb = b.strip()
        if not ca or not cb:
            return False
        if ca == cb:
            return True
        if ca in cb or cb in ca:
            return True
        bare_a = ca.rstrip(";{}(),.").strip()
        bare_b = cb.rstrip(";{}(),.").strip()
        if len(bare_a) >= 5 and len(bare_b) >= 5:
            if bare_a == bare_b or bare_a in bare_b or bare_b in bare_a:
                return True
        # Token overlap check for statements
        words_a = set(re.findall(r"[A-Za-z0-9_]{3,}", ca))
        words_b = set(re.findall(r"[A-Za-z0-9_]{3,}", cb))
        common_words = words_a.intersection(words_b)
        common_words -= {
            "public", "private", "protected", "static", "final", "void", "return",
            "this", "null", "true", "false", "new", "class", "interface"
        }
        return len(common_words) >= 1

    def _is_suggestion_safe(
        self,
        body: str,
        path: str,
        line: int,
        target_code: Optional[str],
        parsed_diff: ParsedDiff,
        was_snapped: bool,
        is_valid_line: bool,
    ) -> bool:
        if not is_valid_line:
            return False

        if "```suggestion" not in body:
            return True

        if path not in parsed_diff.line_contents or line not in parsed_diff.line_contents[path]:
            return False

        target_line_content = parsed_diff.line_contents[path][line].strip()
        if not target_line_content:
            return False

        # If snapped by proximity across lines, only allow suggestion if target_code actually matches the snapped line
        if was_snapped:
            if target_code and self._is_code_similar(target_code, target_line_content):
                return True
            return False

        # Structural brackets / empty lines should not be overwritten by multi-line code suggestions
        sugg_match = re.search(r"```suggestion\r?\n(.*?)\r?\n```", body, re.DOTALL)
        if sugg_match:
            sugg_code = sugg_match.group(1).strip()
            if target_line_content in ("}", "{", ");", "};") and sugg_code not in ("}", "{", ");", "};"):
                return False

            # Reject suggestions that strip 'this.' and produce self-referential calls (e.g. holder.is(holder) when target was this.holder.is(holder))
            self_calls = re.findall(r"\b([A-Za-z0-9_]+)\.[A-Za-z0-9_]+\(\s*\1\s*\)", sugg_code)
            for var_name in self_calls:
                if f"this.{var_name}" in target_line_content:
                    return False

        # If the AI specified target_code, verify that the line at `line` actually resembles it
        if target_code:
            return self._is_code_similar(target_code, target_line_content)

        # If no target_code was specified, check similarity with suggestion lines
        sugg_match = re.search(r"```suggestion\r?\n(.*?)\r?\n```", body, re.DOTALL)
        if sugg_match:
            sugg_code = sugg_match.group(1).strip()
            first_sugg_line = next((l.strip() for l in sugg_code.splitlines() if l.strip()), "")
            if first_sugg_line:
                return self._is_code_similar(first_sugg_line, target_line_content)

        return True

    def _is_actionable_comment(self, body: str, item: Optional[Dict[str, Any]] = None) -> bool:
        """
        Validates that a comment actually proposes an actionable change and does not
        retract itself (e.g. concluding '[DISMISSED]', '[NO_ISSUE]', 'No issue', or actionable=false).
        """
        if item is not None and not item.get("actionable", True):
            return False

        clean = body.strip().rstrip(".! \t\r\n").lower()
        if not clean:
            return False

        # Explicit dismissal markers anywhere or at end
        if clean.endswith("[dismissed]") or clean.endswith("[no_issue]") or clean.endswith("[discard]"):
            return False
        if "[dismissed]" in clean or "[no_issue]" in clean or "[discard]" in clean:
            return False

        # Check ending of comment (where retractions typically appear)
        last_sentence = clean.splitlines()[-1].strip() if clean.splitlines() else clean
        retraction_endings = [
            "no issue",
            "no issues",
            "no issue here",
            "no issues here",
            "no action needed",
            "no action required",
            "no changes needed",
            "no change needed",
            "no further changes needed",
            "never mind",
            "nevermind",
            "just noting",
            "all good",
            "looks good",
            "this is fine",
            "this is acceptable",
            "this is correct",
            "this is valid",
            "fine as is",
            "correct as is",
            "acceptable as is",
        ]
        for end in retraction_endings:
            if last_sentence.endswith(end):
                return False

        # Substring / pattern check for self-retraction phrases
        retraction_patterns = [
            r"\bso\s+(?:this\.?|it)\s+is\s+mandatory(?: here)?[\.!]\s*no issue",
            r"\bwait,?\s+the\s+parameter\s+shadows\b.*?no issue",
            r"\bno action (?:is )?needed\b",
            r"\bno changes? (?:are |is )?needed\b",
            r"\bno further changes? (?:are |is )?needed\b",
            r"\bno issue(?:s)? (?:found|detected)\b",
            r"\bactually,?\s+(?:this is|it is|it's)\s+(?:fine|acceptable|correct|valid)\b",
        ]
        for pat in retraction_patterns:
            if re.search(pat, clean, re.IGNORECASE):
                return False

        return True

    def _is_layout_inversion_hallucination(self, path: str, body: str, line: Optional[int] = None) -> bool:
        """
        Detects before/after diff inversion hallucinations where the AI claims a class layout
        ordering violation (e.g. constructor should be above static factories, or static factories
        below constructors) when the surrounding source code already places them in that exact order.
        """
        if not path or not path.endswith(".java") or not getattr(self, "inspector", None):
            return False

        body_lower = body.lower()
        layout_triggers = [
            "class layout",
            "member sequence",
            "placed directly below constructor",
            "placed directly above",
            "below constructors",
            "above constructors",
            "below static factories",
            "above static factories",
            "static factory methods",
        ]
        if not any(trigger in body_lower for trigger in layout_triggers):
            return False

        resolved_path = self.inspector._resolve_safe_path(path)
        if not resolved_path or not os.path.isfile(resolved_path):
            return False

        try:
            with open(resolved_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return False

        file_text = "".join(lines)
        class_match = re.search(r"\b(?:public\s+|protected\s+|private\s+)?(?:abstract\s+)?class\s+(\w+)", file_text)
        if not class_match:
            return False
        class_name = class_match.group(1)

        # Find line numbers of constructors (1-indexed)
        constructor_pattern = re.compile(r"^\s*(?:public|protected|private)?\s*" + re.escape(class_name) + r"\s*\(")
        constructor_lines = [i for i, l in enumerate(lines, start=1) if constructor_pattern.search(l)]

        # Find line numbers of static factory/helper methods
        static_method_pattern = re.compile(r"^\s*public\s+static\s+(?:<[^>]+>\s+)?[A-Za-z0-9_<>\[\],\s]+\s+(\w+)\s*\(")
        static_factory_lines = [i for i, l in enumerate(lines, start=1) if static_method_pattern.search(l)]

        # Check Claim 1: Constructor should be above static factories / static factories below constructors
        claims_constructor_above_static = (
            re.search(r"static (?:factory )?methods?.*(?:below|after).*constructor", body_lower)
            or re.search(r"constructor.*(?:above|before).*static", body_lower)
            or "must be placed directly below constructors" in body_lower
            or "should be placed directly above the static factory methods" in body_lower
            or "above any static factory methods" in body_lower
        )
        if claims_constructor_above_static and constructor_lines and static_factory_lines:
            last_constructor = max(constructor_lines)
            first_static = min(static_factory_lines)
            if last_constructor < first_static:
                # The constructor is ALREADY above all static factory methods!
                return True

        # Check Claim 2: Static factories above instance methods / instance methods below static factories
        claims_instance_below_static = (
            re.search(r"instance methods?.*(?:below|after).*static", body_lower)
            or re.search(r"static.*(?:above|before).*instance", body_lower)
            or "should remain below the static factories" in body_lower
        )
        if claims_instance_below_static and static_factory_lines:
            last_static = max(static_factory_lines)
            if line is not None and line > last_static:
                if not constructor_lines or max(constructor_lines) < min(static_factory_lines):
                    return True

        return False

    def _get_enclosing_method_parameters(self, lines: List[str], target_line: int) -> set[str]:
        """
        Finds the method enclosing target_line (1-indexed) in a Java source file
        and returns the set of parameter names declared by that method.
        """
        if target_line < 1 or target_line > len(lines):
            return set()

        header_lines = []
        found_signature = False
        for i in range(target_line - 1, max(-1, target_line - 60), -1):
            line_str = lines[i].strip()
            if re.search(r"\b(?:class|interface|enum|record)\s+\w+", line_str) and "(" not in line_str:
                break
            header_lines.insert(0, line_str)
            if "(" in line_str:
                found_signature = True
                break

        if not found_signature:
            return set()

        full_header = " ".join(header_lines)
        m = re.search(r"\b([a-zA-Z0-9_]+)\s*\(([^)]*)\)", full_header)
        if not m:
            return set()

        param_str = m.group(2).strip()
        if not param_str:
            return set()

        param_names = set()
        raw_params = []
        curr = []
        depth = 0
        for ch in param_str:
            if ch == '<':
                depth += 1
            elif ch == '>':
                depth -= 1
            elif ch == ',' and depth == 0:
                raw_params.append("".join(curr).strip())
                curr = []
                continue
            curr.append(ch)
        if curr:
            raw_params.append("".join(curr).strip())

        for p in raw_params:
            words = [w for w in re.split(r"[\s\[\]]+", p.strip()) if w and not w.startswith("@")]
            if words:
                param_name = words[-1]
                if re.match(r"^[a-zA-Z0-9_]+$", param_name):
                    param_names.add(param_name)

        return param_names

    def _is_this_shadowing_hallucination(self, path: str, body: str, line: Optional[int] = None) -> bool:
        """
        Detects false-positive comments alleging unnecessary 'this.' qualifiers when
        the accessed member name is actually shadowed by a parameter of the enclosing method.
        """
        if not path or not path.endswith(".java") or not getattr(self, "inspector", None) or line is None:
            return False

        body_lower = body.lower()
        if "this." not in body_lower and "this prefix" not in body_lower and "this qualifier" not in body_lower:
            return False

        removal_cues = ["unnecessary", "redundant", "avoid", "remove", "omit", "do not use", "qualifier", "no field shadowing"]
        if not any(cue in body_lower for cue in removal_cues):
            return False

        resolved_path = self.inspector._resolve_safe_path(path)
        if not resolved_path or not os.path.isfile(resolved_path):
            return False

        try:
            with open(resolved_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            return False

        if line < 1 or line > len(lines):
            return False

        target_line_text = lines[line - 1]
        this_matches = set(re.findall(r"\bthis\.([a-zA-Z0-9_]+)\b", target_line_text))
        if not this_matches:
            this_matches = set(re.findall(r"\bthis\.([a-zA-Z0-9_]+)\b", body))

        if not this_matches:
            return False

        param_names = self._get_enclosing_method_parameters(lines, line)
        for var_name in this_matches:
            if var_name in param_names:
                return True

        return False

    def _is_formatter_conflict_hallucination(
        self,
        path: str,
        body: str,
        line: Optional[int],
        parsed_diff: Optional[ParsedDiff] = None,
        target_code: Optional[str] = None,
    ) -> bool:
        """
        Detects formatter conflict hallucinations where the AI claims that empty method bodies
        or blocks should not be collapsed into a single line (demanding expansion across multiple lines),
        or flags empty static initialization hooks (like `public static void init() {}`) as dead code.
        Automated formatters (Spotless/Immaculate) enforce `{}` on a single line, and multi-loader mods
        use empty static `init()` hooks to force classloading and static initialization.
        """
        body_lower = body.lower()

        # Phrases indicating the AI is demanding multi-line expansion of empty blocks / {}
        expand_braces_triggers = [
            "collapsed into a single line",
            "collapsed onto a single line",
            "expand the curly braces",
            "expand the braces",
            "curly braces onto separate lines",
            "braces onto separate lines",
            "braces should be on separate lines",
            "expand the method body",
            "separate lines or remove the method",
            "method bodies, even when empty",
            "even when empty, should not be collapsed",
            "bodies, even when empty, should not be collapsed",
            "collapsed into {}",
            "collapsed to {}",
        ]
        has_expand_trigger = any(t in body_lower for t in expand_braces_triggers) or (
            ("expand" in body_lower or "separate line" in body_lower)
            and ("brace" in body_lower or "empty method" in body_lower or "{}" in body)
        )

        # Flagging init() or lifecycle hooks as dead code
        dead_code_init_trigger = (
            ("dead code" in body_lower or "unused method" in body_lower or "remove the method" in body_lower)
            and ("init()" in body_lower or "init (" in body_lower or "lifecycle" in body_lower)
        )

        # Check if suggestion in body expands single-line {} onto multiple lines
        sugg_match = re.search(r"```suggestion\r?\n(.*?)\r?\n```", body, re.DOTALL)
        has_sugg_expansion = False
        sugg_code = ""
        if sugg_match:
            sugg_code = sugg_match.group(1)
            if len(sugg_code.splitlines()) > 1:
                has_sugg_expansion = True

        if not (has_expand_trigger or dead_code_init_trigger or has_sugg_expansion):
            return False

        # Retrieve target line content if available
        target_text = target_code or ""
        if not target_text and parsed_diff and path in parsed_diff.line_contents and line is not None and line in parsed_diff.line_contents[path]:
            target_text = parsed_diff.line_contents[path][line]

        if not target_text and getattr(self, "inspector", None) and line is not None:
            resolved_path = self.inspector._resolve_safe_path(path)
            if resolved_path and os.path.isfile(resolved_path):
                try:
                    with open(resolved_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                        if 1 <= line <= len(lines):
                            target_text = lines[line - 1]
                except Exception:
                    pass

        if has_sugg_expansion and target_text:
            if "{}" in target_text or re.search(r"\{\s*\}", target_text):
                clean_sugg = [re.sub(r"[\s\{\}]+$", "", l.strip()) for l in sugg_code.splitlines() if re.sub(r"[\s\{\}]+$", "", l.strip())]
                clean_target = [re.sub(r"[\s\{\}]+$", "", l.strip()) for l in target_text.splitlines() if re.sub(r"[\s\{\}]+$", "", l.strip())]
                if clean_sugg == clean_target:
                    return True

        if has_expand_trigger:
            if not target_text:
                return True
            if "{}" in target_text or re.search(r"\{\s*\}", target_text) or "init()" in target_text or "init" in target_text:
                return True
            if sugg_match:
                sc = sugg_code.strip()
                if re.fullmatch(r"\{[\s\r\n]*\}", sc) or ("{" in sc and "}" in sc and not any(c.isalnum() for c in sc.replace("public", "").replace("static", "").replace("void", ""))):
                    return True

        if dead_code_init_trigger:
            if not target_text or "init" in target_text.lower() or "{}" in target_text:
                return True

        return False

    def _is_service_signature_simplification_hallucination(self, path: str, body: str) -> bool:
        """
        Detects false positives where the AI suggests removing ResourceKey parameters from
        service factory methods (like RegistrationService#createDataComponents) to force
        consistency with createItems/createBlocks. DataComponentType can exist in custom
        component registries, so the SPI intentionally accepts an explicit ResourceKey parameter.
        """
        body_lower = body.lower()
        if "createdatacomponents" in body_lower:
            triggers = ["resourcekey", "only take", "simplified to", "registries.data_component_type", "implicit"]
            if any(term in body_lower for term in triggers):
                return True
        return False

    def _is_specialized_registrar_flawed_delegation(self, path: str, body: str) -> bool:
        """
        Detects false positives where the AI suggests composing or delegating to a generic
        base registrar (such as FabricRegistrar<T>) inside specialized registrars (Items, Blocks,
        Entities). Specialized registrars must return covariant holder subtypes (ItemHolder, BlockHolder);
        delegating to a generic registrar breaks covariance and forces redundant instance allocations
        and re-wrapping.
        """
        body_lower = body.lower()
        delegation_words = ["delegat", "compos", "forward"]
        registrar_targets = ["fabricregistrar", "generic registrar", "base registrar", "registrar<t>"]
        specialized_words = ["specialized registrar", "fabricitemsregistrar", "fabricblocksregistrar", "subclass", "concrete subclass"]

        has_delegation = any(w in body_lower for w in delegation_words)
        has_registrar_target = any(t in body_lower for t in registrar_targets)
        has_specialized = any(s in body_lower for s in specialized_words)

        if has_delegation and has_registrar_target and has_specialized:
            return True
        return False

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
            target_code = item.get("target_code")
            raw_sev = str(item.get("severity", "WARNING")).strip().upper()
            severity = raw_sev if raw_sev in severity_rank else "WARNING"

            if not path or line is None or not body:
                continue

            # Discard self-retracted comments where the AI thought out loud and concluded no issue / no action needed
            if not self._is_actionable_comment(body, item):
                logger.info(f"Discarding non-actionable / self-retracted comment on '{path}:{line}': {body[:60]}...")
                continue

            # Discard before/after layout inversion hallucinations where class members are already correctly ordered
            if self._is_layout_inversion_hallucination(path, body, line):
                logger.info(f"Discarding layout inversion hallucination on '{path}:{line}': {body[:60]}...")
                continue

            # Discard false positive comments attacking 'this.' when 'this.' resolves parameter shadowing
            if self._is_this_shadowing_hallucination(path, body, line):
                logger.info(f"Discarding false positive on '{path}:{line}': 'this.' is mandatory due to parameter shadowing: {body[:60]}...")
                continue

            # Discard formatter conflict hallucinations demanding expansion of single-line {} or flagging init() {} as dead code
            if self._is_formatter_conflict_hallucination(path, body, line, parsed_diff, target_code=target_code):
                logger.info(f"Discarding formatter conflict hallucination on '{path}:{line}': {body[:60]}...")
                continue

            # Discard false positives suggesting removal of ResourceKey from service factory methods
            if self._is_service_signature_simplification_hallucination(path, body):
                logger.info(f"Discarding false positive on '{path}:{line}': SPI factory parameterization is required: {body[:60]}...")
                continue

            # Discard false positives suggesting flawed delegation from specialized registrars to base generic registrars
            if self._is_specialized_registrar_flawed_delegation(path, body):
                logger.info(f"Discarding flawed registrar delegation on '{path}:{line}': breaking covariance: {body[:60]}...")
                continue

            # Ensure line number is a positive int
            try:
                line = int(line)
                if line <= 0:
                    continue
            except (ValueError, TypeError):
                continue

            # 1. Target code verification & line realignment
            extracted_target = target_code
            if not extracted_target:
                # Try to extract code snippet from backticks in comment body
                backtick_matches = re.findall(r"`([^`]{8,})`", body)
                if backtick_matches:
                    extracted_target = backtick_matches[0]

            if extracted_target:
                matched_line = parsed_diff.find_matching_line(path, extracted_target, preferred_line=line)
                if matched_line and matched_line != line:
                    logger.info(f"Realigned comment on '{path}' from line {line} to line {matched_line} (matched '{extracted_target[:40]}...')")
                    line = matched_line

            # 2. Strict line-in-diff validation & tight proximity snapping (<= 3 lines)
            is_valid_line = parsed_diff.is_line_in_diff(path, line)
            was_snapped = False
            if not is_valid_line:
                closest = parsed_diff.get_closest_valid_line(path, line, max_distance=3)
                if closest is not None:
                    line = closest
                    is_valid_line = True
                    was_snapped = True

            # 3. Suggestion safety verification & no-op suggestion discard
            if "```suggestion" in body:
                # Discard no-op / redundant suggestions where target line already matches suggested code
                if path in parsed_diff.line_contents and line in parsed_diff.line_contents[path]:
                    sugg_match = re.search(r"```suggestion\r?\n(.*?)\r?\n```", body, re.DOTALL)
                    if sugg_match:
                        sugg_code = sugg_match.group(1).strip()
                        sugg_lines = [l.strip() for l in sugg_code.splitlines() if l.strip()]
                        if sugg_lines:
                            target_lines = [
                                parsed_diff.line_contents[path].get(line + idx, "").strip()
                                for idx in range(len(sugg_lines))
                            ]
                            if target_lines == sugg_lines:
                                logger.info(
                                    f"Discarding redundant / no-op suggestion comment on '{path}:{line}' "
                                    f"(target code already matches suggested code): {body[:60]}..."
                                )
                                continue
                # Discard bug-injecting suggestions that strip 'this.' to create self-referential calls (e.g. holder.is(holder))
                sugg_match = re.search(r"```suggestion\r?\n(.*?)\r?\n```", body, re.DOTALL)
                if sugg_match:
                    sugg_code = sugg_match.group(1).strip()
                    target_line_content = parsed_diff.line_contents.get(path, {}).get(line, "")
                    self_calls = re.findall(r"\b([A-Za-z0-9_]+)\.[A-Za-z0-9_]+\(\s*\1\s*\)", sugg_code)
                    if any(f"this.{v}" in target_line_content for v in self_calls):
                        logger.info(
                            f"Discarding bug-injecting suggestion comment on '{path}:{line}' "
                            f"(suggestion creates self-referential call): {body[:60]}..."
                        )
                        continue

                # Discard suggestions that expand single-line empty braces {} across multiple lines
                sugg_match = re.search(r"```suggestion\r?\n(.*?)\r?\n```", body, re.DOTALL)
                if sugg_match:
                    sugg_code = sugg_match.group(1)
                    target_line_content = parsed_diff.line_contents.get(path, {}).get(line, "")
                    if "{}" in target_line_content or re.search(r"\{\s*\}", target_line_content):
                        sugg_stripped_statements = [
                            re.sub(r"[\s\{\}]+$", "", l.strip())
                            for l in sugg_code.splitlines()
                            if re.sub(r"[\s\{\}]+$", "", l.strip())
                        ]
                        target_stripped_statements = [
                            re.sub(r"[\s\{\}]+$", "", l.strip())
                            for l in target_line_content.splitlines()
                            if re.sub(r"[\s\{\}]+$", "", l.strip())
                        ]
                        if sugg_stripped_statements == target_stripped_statements and len(sugg_code.splitlines()) > 1:
                            logger.info(
                                f"Discarding formatter conflict suggestion on '{path}:{line}' "
                                f"(expands empty braces onto multiple lines): {body[:60]}..."
                            )
                            continue

                if not self._is_suggestion_safe(body, path, line, extracted_target, parsed_diff, was_snapped, is_valid_line):
                    lang = self._get_code_language(path)
                    body = re.sub(r"```suggestion\b", f"```{lang}", body)
                    logger.warning(
                        f"Comment on '{path}:{line}' has unsafe suggestion for target line; "
                        f"converted suggestion block to regular ```{lang} code block."
                    )

            # 4. Indentation alignment for validated GitHub 1-click suggestions
            if is_valid_line and "```suggestion" in body:
                body = self._align_suggestion_indentation(body, path, line, parsed_diff)

            validated.append({
                "path": path,
                "line": line,
                "body": body,
                "severity": severity,
                "is_valid_line": is_valid_line,
            })

        # Stable sort by severity: CRITICAL -> WARNING -> SUGGESTION
        validated.sort(key=lambda c: severity_rank.get(c.get("severity", "WARNING"), 99))
        return validated


class QuotaExceededException(Exception):
    pass


class HighDemandException(Exception):
    pass

