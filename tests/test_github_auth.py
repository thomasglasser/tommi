import unittest
from unittest.mock import patch, MagicMock
from src.github_auth import GitHubAuthManager

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

if __name__ == "__main__":
    unittest.main()
