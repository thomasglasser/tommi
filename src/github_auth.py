import logging
from typing import Optional
from github import Github, Auth, GithubIntegration, GithubException
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

logger = logging.getLogger("tommi.auth")


def _is_transient_github_error(exc: BaseException) -> bool:
    """Returns True if the exception represents a transient network or server error that should be retried."""
    if isinstance(exc, GithubException):
        if exc.status == 404:
            return False
        if exc.status in (429, 500, 502, 503, 504) or (exc.status is not None and exc.status >= 500):
            return True
        err_msg = str(exc).lower()
        if any(keyword in err_msg for keyword in ("504", "502", "503", "500", "timeout", "timed out", "gateway")):
            return True
        return False
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        if exc.response is not None and (exc.response.status_code in (429, 500, 502, 503, 504) or exc.response.status_code >= 500):
            return True
    err_str = str(exc).lower()
    if any(keyword in err_str for keyword in ("timeout", "timed out", "gateway", "504", "502", "503", "500", "connection reset", "connection aborted")):
        return True
    return False


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
        self._last_auth_error: Optional[str] = None

        if self.app_id and self.private_key:
            try:
                app_auth = Auth.AppAuth(self.app_id, self.private_key)
                self.integration = GithubIntegration(auth=app_auth)
                logger.info(f"Initialized GitHub App Authentication (App ID: {self.app_id})")
            except Exception as e:
                logger.warning(f"Failed to initialize GitHub App Auth: {e}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_transient_github_error),
        reraise=True,
    )
    def _fetch_app_token(self, owner: str, repo: str) -> str:
        if not self.integration:
            raise ValueError("GitHubIntegration not initialized.")
        installation = self.integration.get_repo_installation(owner, repo)
        access_token = self.integration.get_access_token(installation.id)
        return access_token.token

    def get_token_for_repo(self, repo_full_name: str) -> Optional[str]:
        """
        Returns an authenticated installation access token string for the given repository,
        or the fallback token if configured.
        """
        self._last_auth_error = None
        if self.integration and "/" in repo_full_name:
            owner, repo = repo_full_name.split("/", 1)
            try:
                token = self._fetch_app_token(owner, repo)
                logger.info(f"Generated installation token for repository '{repo_full_name}'")
                return token
            except Exception as e:
                self._last_auth_error = str(e)
                logger.warning(f"Could not get App installation token for '{repo_full_name}': {e}. Falling back to default token.")

        return self.token

    def get_client_for_repo(self, repo_full_name: str) -> Github:
        """
        Returns an authenticated Github client for the given repository.
        """
        token = self.get_token_for_repo(repo_full_name)
        if token:
            return Github(auth=Auth.Token(token))

        error_details = f": {self._last_auth_error}" if self._last_auth_error else "."
        raise ValueError(f"No valid GitHub authentication available for repository '{repo_full_name}'{error_details}")
