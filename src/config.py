import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class TommiConfig:
    github_token: str
    gemini_api_key: str
    github_repository: str
    pr_number: int
    comment_id: Optional[int] = None
    comment_body: str = ""
    model_name: str = "auto"
    strictness: str = "standard"  # standard, strict, style-only, bugs-only
    tommi_repo: str = "thomasglasser/tommi"

    @classmethod
    def from_env(cls) -> "TommiConfig":
        github_token = os.environ.get("GITHUB_TOKEN", "").strip()
        gemini_api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        github_repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
        pr_number_str = os.environ.get("PR_NUMBER", "").strip()

        if not github_token:
            raise ValueError("Missing GITHUB_TOKEN environment variable.")
        if not gemini_api_key:
            raise ValueError("Missing GEMINI_API_KEY environment variable.")
        if not github_repository:
            raise ValueError("Missing GITHUB_REPOSITORY environment variable.")
        if not pr_number_str or not pr_number_str.isdigit():
            raise ValueError(f"Invalid or missing PR_NUMBER environment variable: '{pr_number_str}'")

        comment_id_str = os.environ.get("COMMENT_ID", "").strip()
        comment_id = int(comment_id_str) if comment_id_str.isdigit() else None
        comment_body = os.environ.get("COMMENT_BODY", "").strip()

        model_name = os.environ.get("MODEL_NAME", "auto").strip()
        strictness = os.environ.get("STRICTNESS", "standard").strip()
        tommi_repo = os.environ.get("TOMMI_REPO", "thomasglasser/tommi").strip()

        return cls(
            github_token=github_token,
            gemini_api_key=gemini_api_key,
            github_repository=github_repository,
            pr_number=int(pr_number_str),
            comment_id=comment_id,
            comment_body=comment_body,
            model_name=model_name,
            strictness=strictness,
            tommi_repo=tommi_repo,
        )
