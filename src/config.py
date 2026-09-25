import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class TommiConfig:
    gemini_api_key: str
    github_repository: str
    pr_number: int
    github_token: Optional[str] = None
    app_id: Optional[str] = None
    private_key: Optional[str] = None
    comment_id: Optional[int] = None
    comment_body: str = ""
    model_name: str = "auto"
    tommi_repo: str = "thomasglasser/tommi"
    in_reply_to_id: Optional[int] = None
    diff_hunk: str = ""
    file_path: str = ""
    event_name: str = ""
    is_merged: bool = False
    comment_author: str = ""
    comment_author_type: str = ""
    thinking_budget: int = 2048
    max_inline_comments: Optional[int] = None

    @classmethod
    def from_env(cls) -> "TommiConfig":
        github_token = os.environ.get("GITHUB_TOKEN", "").strip() or None
        app_id = os.environ.get("APP_ID", "").strip() or None
        private_key = os.environ.get("PRIVATE_KEY", "").strip() or None

        gemini_api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        github_repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
        pr_number_str = os.environ.get("PR_NUMBER", "").strip()

        if not github_token and not (app_id and private_key):
            raise ValueError("Must provide either GITHUB_TOKEN or both APP_ID and PRIVATE_KEY.")
        if not gemini_api_key:
            raise ValueError("Missing GEMINI_API_KEY environment variable.")
        if not github_repository:
            raise ValueError("Missing GITHUB_REPOSITORY environment variable.")
        if not pr_number_str or not pr_number_str.isdigit():
            raise ValueError(f"Invalid or missing PR_NUMBER environment variable: '{pr_number_str}'")

        comment_id_str = os.environ.get("COMMENT_ID", "").strip()
        comment_id = int(comment_id_str) if comment_id_str.isdigit() else None
        comment_body = os.environ.get("COMMENT_BODY", "").strip()
        comment_author = os.environ.get("COMMENT_AUTHOR", "").strip()
        comment_author_type = os.environ.get("COMMENT_AUTHOR_TYPE", "").strip()

        in_reply_to_id_str = os.environ.get("IN_REPLY_TO_ID", "").strip()
        in_reply_to_id = int(in_reply_to_id_str) if in_reply_to_id_str.isdigit() else None
        diff_hunk = os.environ.get("DIFF_HUNK", "").strip()
        file_path = os.environ.get("FILE_PATH", "").strip()

        event_name = os.environ.get("EVENT_NAME", "").strip()
        is_merged_str = os.environ.get("IS_MERGED", "false").strip().lower()
        is_merged = is_merged_str in ("true", "1", "yes")

        model_name = os.environ.get("MODEL_NAME", "auto").strip()
        tommi_repo = os.environ.get("TOMMI_REPO", "thomasglasser/tommi").strip()

        thinking_budget_str = os.environ.get("THINKING_BUDGET", "2048").strip().lower()
        if thinking_budget_str in ("0", "off", "false", "none", "disable", "disabled"):
            thinking_budget = 0
        elif thinking_budget_str.isdigit():
            thinking_budget = int(thinking_budget_str)
        else:
            thinking_budget = 2048

        max_inline_comments_str = os.environ.get("MAX_INLINE_COMMENTS", "").strip()
        max_inline_comments = int(max_inline_comments_str) if max_inline_comments_str.isdigit() and int(max_inline_comments_str) > 0 else None

        return cls(
            gemini_api_key=gemini_api_key,
            github_repository=github_repository,
            pr_number=int(pr_number_str),
            github_token=github_token,
            app_id=app_id,
            private_key=private_key,
            comment_id=comment_id,
            comment_body=comment_body,
            model_name=model_name,
            tommi_repo=tommi_repo,
            in_reply_to_id=in_reply_to_id,
            diff_hunk=diff_hunk,
            file_path=file_path,
            event_name=event_name,
            is_merged=is_merged,
            comment_author=comment_author,
            comment_author_type=comment_author_type,
            thinking_budget=thinking_budget,
            max_inline_comments=max_inline_comments,
        )

    @classmethod
    def for_local(
        cls,
        gemini_api_key: Optional[str] = None,
        model_name: str = "auto",
        thinking_budget: int = 2048,
        workspace_dir: Optional[str] = None,
        tommi_repo: str = "thomasglasser/tommi",
    ) -> "TommiConfig":
        """
        Creates a TommiConfig tailored for local offline diff reviews without requiring GitHub tokens or PR IDs.
        """
        key = (gemini_api_key or os.environ.get("GEMINI_API_KEY", "")).strip()

        # If not in env, search .env files
        if not key:
            candidates = [
                os.path.join(workspace_dir or os.getcwd(), ".env"),
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
            ]
            for env_path in candidates:
                if os.path.isfile(env_path):
                    try:
                        with open(env_path, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line.startswith("GEMINI_API_KEY="):
                                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                                    break
                    except Exception:
                        pass
                if key:
                    break

        if not key:
            raise ValueError(
                "Missing GEMINI_API_KEY. Please set the GEMINI_API_KEY environment variable "
                "or define it in a .env file."
            )

        return cls(
            gemini_api_key=key,
            github_repository="local/workspace",
            pr_number=0,
            model_name=model_name,
            tommi_repo=tommi_repo,
            thinking_budget=thinking_budget,
            event_name="local_review",
        )
