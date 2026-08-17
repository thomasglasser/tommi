import unittest
from unittest.mock import patch, MagicMock
from src.main import main
from src.reviewer import HighDemandException, QuotaExceededException

class TestMainErrorHandling(unittest.TestCase):
    @patch("src.main.GitHubCommenter")
    @patch("src.main.GitHubAuthManager")
    @patch("src.main.TommiConfig.from_env")
    @patch("src.main.TommiReviewer")
    def test_main_handles_high_demand(self, mock_reviewer_cls, mock_from_env, mock_auth_cls, mock_commenter_cls):
        mock_config = MagicMock()
        mock_config.event_name = "issue_comment"
        mock_config.is_merged = False
        mock_config.comment_body = "/tommi review"
        mock_config.github_repository = "test/repo"
        mock_config.pr_number = 1
        mock_config.comment_id = 123
        mock_from_env.return_value = mock_config

        mock_commenter = MagicMock()
        mock_commenter_cls.return_value = mock_commenter

        mock_reviewer = MagicMock()
        mock_reviewer.review_pr.side_effect = HighDemandException("High demand")
        mock_reviewer_cls.return_value = mock_reviewer

        # main() should catch HighDemandException and post a friendly comment without crashing (sys.exit(1))
        main()

        mock_commenter.reply_to_comment.assert_called_once()
        self.assertIn("busy", mock_commenter.reply_to_comment.call_args[0][0])
        self.assertIn("high demand", mock_commenter.reply_to_comment.call_args[0][0])

    @patch("src.main.GitHubCommenter")
    @patch("src.main.GitHubAuthManager")
    @patch("src.main.TommiConfig.from_env")
    @patch("src.main.TommiReviewer")
    def test_main_handles_quota_exceeded(self, mock_reviewer_cls, mock_from_env, mock_auth_cls, mock_commenter_cls):
        mock_config = MagicMock()
        mock_config.event_name = "issue_comment"
        mock_config.is_merged = False
        mock_config.comment_body = "/tommi review"
        mock_config.github_repository = "test/repo"
        mock_config.pr_number = 1
        mock_config.comment_id = 123
        mock_from_env.return_value = mock_config

        mock_commenter = MagicMock()
        mock_commenter_cls.return_value = mock_commenter

        mock_reviewer = MagicMock()
        mock_reviewer.review_pr.side_effect = QuotaExceededException("Quota exceeded")
        mock_reviewer_cls.return_value = mock_reviewer

        main()

        mock_commenter.reply_to_comment.assert_called_once()
        self.assertIn("resting", mock_commenter.reply_to_comment.call_args[0][0])

    @patch("src.main.GitHubCommenter")
    @patch("src.main.GitHubAuthManager")
    @patch("src.main.TommiConfig.from_env")
    @patch("src.main.TommiReviewer")
    def test_main_handles_generic_503_exception(self, mock_reviewer_cls, mock_from_env, mock_auth_cls, mock_commenter_cls):
        mock_config = MagicMock()
        mock_config.event_name = "issue_comment"
        mock_config.is_merged = False
        mock_config.comment_body = "/tommi review"
        mock_config.github_repository = "test/repo"
        mock_config.pr_number = 1
        mock_config.comment_id = 123
        mock_from_env.return_value = mock_config

        mock_commenter = MagicMock()
        mock_commenter_cls.return_value = mock_commenter

        mock_reviewer = MagicMock()
        mock_reviewer.review_pr.side_effect = RuntimeError("503 UNAVAILABLE: This model is currently experiencing high demand.")
        mock_reviewer_cls.return_value = mock_reviewer

        main()

        mock_commenter.reply_to_comment.assert_called_once()
        self.assertIn("high demand", mock_commenter.reply_to_comment.call_args[0][0])

if __name__ == "__main__":
    unittest.main()
