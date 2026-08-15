import json
import logging
from typing import List, Dict, Any, Optional
import requests
from google import genai
from google.genai import types

from src.config import TommiConfig
from src.diff_parser import parse_unified_diff, ParsedDiff
from src.rules_loader import load_all_rules, LoadedRules
from src.models_resolver import resolve_model_name

logger = logging.getLogger("tommi.reviewer")


class TommiReviewer:
    def __init__(self, config: TommiConfig):
        self.config = config
        self.client = genai.Client(api_key=config.gemini_api_key)

    def fetch_pr_diff(self, pr_url: str) -> str:
        """Fetches the raw diff of the PR using GitHub API."""
        headers = {
            "Authorization": f"token {self.config.github_token}",
            "Accept": "application/vnd.github.v3.diff",
        }
        resp = requests.get(pr_url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch PR diff (HTTP {resp.status_code}): {resp.text}")
        return resp.text

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
        model_name = resolve_model_name(self.client, self.config.model_name)

        logger.info(f"Loaded rules ({len(rules.base_rules)} base modules, {len(rules.local_rules)} local files).")
        logger.info(f"Running Gemini review with model '{model_name}'...")

        prompt = self._build_review_prompt(pr_title, pr_body, diff_text, rules)

        try:
            response = self.client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.15,
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

            comments_data = json.loads(raw_json.strip())

        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "quota" in error_str or "exhausted" in error_str:
                raise QuotaExceededException("T.O.M.M.I. has run out of AI API quota for today. Please try again later.") from e
            raise RuntimeError(f"Failed to generate or parse AI review response: {e}") from e

        # Validate and adjust line numbers against parsed diff
        validated_comments = self._validate_comments(comments_data, parsed_diff)
        return validated_comments

    def _build_review_prompt(self, pr_title: str, pr_body: str, diff_text: str, rules: LoadedRules) -> str:
        formatted_rules = rules.format_for_prompt()
        return f"""
You are Thomas Glasser (@thomasglasser), an expert Minecraft/NeoForge mod developer, architect, and strict code reviewer.
You are reviewing a Pull Request in one of your repositories.

### YOUR CODE STANDARDS & EXPECTATIONS:
{formatted_rules}

### PULL REQUEST INFORMATION:
- **Title**: {pr_title}
- **Description**: {pr_body or '(No description provided)'}

### PULL REQUEST DIFF:
```diff
{diff_text}
```

### INSTRUCTIONS:
1. Review the diff strictly against your code style standards, architectural rules, side-safety, performance restrictions, and clean design patterns.
2. Be concise, direct, and instructional in your comments. Point out what is wrong and exactly how to fix it according to your rules.
3. Identify ONLY real violations, bugs, or regressions. Do not leave nitpicks for valid code, and do NOT leave generic praise.
4. Return your comments as a strict JSON array of objects.
5. Each object must have:
   - `path`: The exact relative file path of the file being reviewed (matching the `b/` path in diff).
   - `line`: The exact line number in the NEW version of the file (RIGHT side of diff) where the issue occurs.
   - `body`: Your review comment.
6. If there are no issues found, return an empty array `[]`.
7. Return ONLY the raw JSON array.
"""

    def _validate_comments(self, raw_comments: List[Dict[str, Any]], parsed_diff: ParsedDiff) -> List[Dict[str, Any]]:
        validated = []
        for item in raw_comments:
            path = item.get("path")
            line = item.get("line")
            body = item.get("body")

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
                "body": body
            })
        return validated


class QuotaExceededException(Exception):
    pass
