import unittest
from unittest.mock import patch, MagicMock
from github import GithubException
from src.github_auth import GitHubAuthManager, _is_transient_github_error

class TestGitHubAuthManager(unittest.TestCase):
    def test_token_auth(self):
        auth_mgr = GitHubAuthManager(token="ghp_test_token")
        client = auth_mgr.get_client_for_repo("owner/repo")
        self.assertIsNotNone(client)

    @patch("src.github_auth.GithubIntegration")
    @patch("src.github_auth.Auth.AppAuth")
    def test_app_auth(self, mock_app_auth, mock_integration_cls):
        mock_integration = MagicMock()
        mock_installation = MagicMock()
        mock_installation.id = 9999
        mock_access_token = MagicMock()
        mock_access_token.token = "fake_installation_token"
        mock_integration.get_repo_installation.return_value = mock_installation
        mock_integration.get_access_token.return_value = mock_access_token
        mock_integration_cls.return_value = mock_integration

        auth_mgr = GitHubAuthManager(app_id="12345", private_key="fake_key")
        token = auth_mgr.get_token_for_repo("thomasglasser/tommi")
        self.assertEqual(token, "fake_installation_token")
        client = auth_mgr.get_client_for_repo("thomasglasser/tommi")

        mock_integration.get_repo_installation.assert_called_with("thomasglasser", "tommi")
        self.assertIsNotNone(client)

    @patch("src.github_auth.GithubIntegration")
    @patch("src.github_auth.Auth.AppAuth")
    def test_app_auth_retries_on_504_gateway_timeout(self, mock_app_auth, mock_integration_cls):
        mock_integration = MagicMock()
        mock_installation = MagicMock()
        mock_installation.id = 9999
        mock_access_token = MagicMock()
        mock_access_token.token = "fake_installation_token"
        mock_integration.get_repo_installation.return_value = mock_installation
        
        # First call fails with 504 Gateway Timeout, second succeeds
        mock_integration.get_access_token.side_effect = [
            GithubException(504, {"message": "504 Gateway Timeout"}, None),
            mock_access_token
        ]
        mock_integration_cls.return_value = mock_integration

        auth_mgr = GitHubAuthManager(app_id="12345", private_key="fake_key")
        token = auth_mgr.get_token_for_repo("thomasglasser/tommi")
        self.assertEqual(token, "fake_installation_token")
        self.assertEqual(mock_integration.get_access_token.call_count, 2)

    @patch("src.github_auth.GithubIntegration")
    @patch("src.github_auth.Auth.AppAuth")
    def test_app_auth_no_retry_on_404_not_found(self, mock_app_auth, mock_integration_cls):
        mock_integration = MagicMock()
        mock_integration.get_repo_installation.side_effect = GithubException(404, {"message": "Not Found"}, None)
        mock_integration_cls.return_value = mock_integration

        auth_mgr = GitHubAuthManager(token="fallback_token", app_id="12345", private_key="fake_key")
        token = auth_mgr.get_token_for_repo("thomasglasser/tommi")
        self.assertEqual(token, "fallback_token")
        self.assertEqual(mock_integration.get_repo_installation.call_count, 1)

    def test_is_transient_github_error(self):
        self.assertTrue(_is_transient_github_error(GithubException(504, "Gateway Timeout", None)))
        self.assertTrue(_is_transient_github_error(GithubException(502, "Bad Gateway", None)))
        self.assertTrue(_is_transient_github_error(GithubException(500, "Internal Server Error", None)))
        self.assertTrue(_is_transient_github_error(GithubException(429, "Rate limit exceeded", None)))
        self.assertFalse(_is_transient_github_error(GithubException(404, "Not Found", None)))
        self.assertFalse(_is_transient_github_error(GithubException(401, "Bad credentials", None)))
        self.assertTrue(_is_transient_github_error(TimeoutError("Operation timed out")))

if __name__ == "__main__":
    unittest.main()
