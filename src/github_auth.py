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

    def get_client_for_repo(self, repo_full_name: str) -> Github:
        """
        Returns an authenticated Github client for the given repository.
        If GitHub App credentials are available, dynamically generates an installation token for that repo.
        Otherwise, falls back to the provided token.
        """
        if self.integration and "/" in repo_full_name:
            owner, repo = repo_full_name.split("/", 1)
            try:
                installation = self.integration.get_repo_installation(owner, repo)
                client = installation.get_github_for_installation()
                logger.info(f"Obtained installation client for repository '{repo_full_name}'")
                return client
            except Exception as e:
                logger.warning(f"Could not get App installation for '{repo_full_name}': {e}. Falling back to default token.")

        if self.token:
            return Github(auth=Auth.Token(self.token))

        raise ValueError(f"No valid GitHub authentication available for repository '{repo_full_name}'.")
