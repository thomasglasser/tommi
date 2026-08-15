import logging
from typing import Optional
from github import Github, Auth, GithubIntegration

logger = logging.getLogger("tommi.auth")


class GitHubAuthManager:
    def __init__(
        self,
        token: Optional[str] = None,
        app_id: Optional[str] = None,
        private_key: Optional[str] = None
    ):
        self.token = token.strip() if token else None
        self.app_id = int(app_id.strip()) if (app_id and app_id.strip().isdigit()) else None
        self.private_key = private_key.strip() if private_key else None
        self.integration: Optional[GithubIntegration] = None

        if self.app_id and self.private_key:
            try:
                app_auth = Auth.AppAuth(self.app_id, self.private_key)
                self.integration = GithubIntegration(auth=app_auth)
                logger.info(f"Initialized GitHub App Authentication (App ID: {self.app_id})")
            except Exception as e:
                logger.warning(f"Failed to initialize GitHub App Auth: {e}")

    def get_token_for_repo(self, repo_full_name: str) -> Optional[str]:
        """
        Returns an authenticated installation access token string for the given repository,
        or the fallback token if configured.
        """
        if self.integration and "/" in repo_full_name:
            owner, repo = repo_full_name.split("/", 1)
            try:
                installation = self.integration.get_repo_installation(owner, repo)
                access_token = self.integration.get_access_token(installation.id)
                logger.info(f"Generated installation token for repository '{repo_full_name}'")
                return access_token.token
            except Exception as e:
                logger.warning(f"Could not get App installation token for '{repo_full_name}': {e}. Falling back to default token.")

        return self.token

    def get_client_for_repo(self, repo_full_name: str) -> Github:
        """
        Returns an authenticated Github client for the given repository.
        """
        token = self.get_token_for_repo(repo_full_name)
        if token:
            return Github(auth=Auth.Token(token))

        raise ValueError(f"No valid GitHub authentication available for repository '{repo_full_name}'.")
