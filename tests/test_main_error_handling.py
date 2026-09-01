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

    @patch("src.main.GitHubCommenter")
    @patch("src.main.GitHubAuthManager")
    @patch("src.main.TommiConfig.from_env")
    def test_main_handles_help_command(self, mock_from_env, mock_auth_cls, mock_commenter_cls):
        mock_config = MagicMock()
        mock_config.event_name = "issue_comment"
        mock_config.is_merged = False
        mock_config.comment_body = "/tommi help"
        mock_config.github_repository = "test/repo"
        mock_config.pr_number = 1
        mock_config.comment_id = 123
        mock_from_env.return_value = mock_config

        mock_commenter = MagicMock()
        mock_commenter_cls.return_value = mock_commenter

        main()

        mock_commenter.reply_to_comment.assert_called_once()
        self.assertIn("Commands", mock_commenter.reply_to_comment.call_args[0][0])
        self.assertIn("/tommi review", mock_commenter.reply_to_comment.call_args[0][0])

    @patch("src.main.TommiLearner")
    @patch("src.main.GitHubCommenter")
    @patch("src.main.GitHubAuthManager")
    @patch("src.main.TommiConfig.from_env")
    @patch("src.main.TommiReviewer")
    def test_main_handles_inline_reply_feedback(self, mock_reviewer_cls, mock_from_env, mock_auth_cls, mock_commenter_cls, mock_learner_cls):
        mock_config = MagicMock()
        mock_config.event_name = "pull_request_review_comment"
        mock_config.is_merged = False
        mock_config.comment_body = "/tommi this is actually safe because Context#level() is a ServerLevel"
        mock_config.comment_author = "thomasglasser"
        mock_config.comment_author_type = "User"
        mock_config.github_repository = "test/repo"
        mock_config.tommi_repo = "thomasglasser/tommi"
        mock_config.pr_number = 1
        mock_config.comment_id = 123
        mock_config.in_reply_to_id = 456
        mock_config.file_path = "src/Test.java"
        mock_config.diff_hunk = "@@ -1 +1 @@"
        mock_from_env.return_value = mock_config

        mock_commenter = MagicMock()
        mock_commenter_cls.return_value = mock_commenter

        mock_auth = MagicMock()
        mock_auth_cls.return_value = mock_auth
        mock_tommi_repo = MagicMock()
        mock_tommi_repo.owner.login = "thomasglasser"
        mock_tommi_g = MagicMock()
        mock_tommi_g.get_repo.return_value = mock_tommi_repo
        mock_auth.get_client_for_repo.return_value = mock_tommi_g

        mock_learner = MagicMock()
        mock_learner.process_feedback.return_value = {
            "pr_url": "https://github.com/thomasglasser/tommi/pull/99",
            "learning_plan": {"summary": "Clarify Context#level returns ServerLevel"}
        }
        mock_learner_cls.return_value = mock_learner

        main()

        mock_learner.process_feedback.assert_called_once()
        self.assertEqual(mock_learner.process_feedback.call_args[1]["command_type"], "false-positive")
        self.assertIn("ServerLevel", mock_learner.process_feedback.call_args[1]["feedback_text"])
        mock_commenter.reply_to_comment.assert_called_once()
        self.assertIn("Feedback Processed", mock_commenter.reply_to_comment.call_args[0][0])

    @patch("src.main.TommiLearner")
    @patch("src.main.GitHubCommenter")
    @patch("src.main.GitHubAuthManager")
    @patch("src.main.TommiConfig.from_env")
    @patch("src.main.TommiReviewer")
    def test_main_rejects_feedback_without_write_access(self, mock_reviewer_cls, mock_from_env, mock_auth_cls, mock_commenter_cls, mock_learner_cls):
        mock_config = MagicMock()
        mock_config.event_name = "issue_comment"
        mock_config.is_merged = False
        mock_config.comment_body = "/tommi learn Never do X"
        mock_config.comment_author = "external-contributor"
        mock_config.comment_author_type = "User"
        mock_config.github_repository = "test/repo"
        mock_config.tommi_repo = "thomasglasser/tommi"
        mock_config.pr_number = 1
        mock_config.comment_id = 123
        mock_config.in_reply_to_id = None
        mock_from_env.return_value = mock_config

        mock_commenter = MagicMock()
        mock_commenter_cls.return_value = mock_commenter

        mock_auth = MagicMock()
        mock_auth_cls.return_value = mock_auth
        mock_tommi_repo = MagicMock()
        mock_tommi_repo.owner.login = "thomasglasser"
        mock_tommi_repo.get_collaborator_permission.return_value = "read"
        mock_tommi_g = MagicMock()
        mock_tommi_g.get_repo.return_value = mock_tommi_repo
        mock_auth.get_client_for_repo.return_value = mock_tommi_g

        mock_learner = MagicMock()
        mock_learner_cls.return_value = mock_learner

        main()

        mock_learner.process_feedback.assert_not_called()
        mock_commenter.reply_to_comment.assert_called_once()
        self.assertIn("Permission Denied", mock_commenter.reply_to_comment.call_args[0][0])

    @patch("src.main.GitHubCommenter")
    @patch("src.main.GitHubAuthManager")
    @patch("src.main.TommiConfig.from_env")
    def test_main_handles_unrecognized_tommi_command_with_help(self, mock_from_env, mock_auth_cls, mock_commenter_cls):
        mock_config = MagicMock()
        mock_config.event_name = "issue_comment"
        mock_config.is_merged = False
        mock_config.comment_body = "/tommi unknown-argument"
        mock_config.github_repository = "test/repo"
        mock_config.pr_number = 1
        mock_config.comment_id = 123
        mock_config.in_reply_to_id = None
        mock_from_env.return_value = mock_config

        mock_commenter = MagicMock()
        mock_commenter_cls.return_value = mock_commenter

        main()

        mock_commenter.reply_to_comment.assert_called_once()
        self.assertIn("Commands", mock_commenter.reply_to_comment.call_args[0][0])

    @patch("src.main.GitHubCommenter")
    @patch("src.main.GitHubAuthManager")
    @patch("src.main.TommiConfig.from_env")
    def test_main_ignores_bot_comments(self, mock_from_env, mock_auth_cls, mock_commenter_cls):
        mock_config = MagicMock()
        mock_config.event_name = "issue_comment"
        mock_config.is_merged = False
        mock_config.comment_body = "/tommi help"
        mock_config.comment_author = "t-o-m-m-i-ai-reviewer[bot]"
        mock_config.comment_author_type = "Bot"
        mock_config.github_repository = "test/repo"
        mock_config.pr_number = 1
        mock_config.comment_id = 123
        mock_config.in_reply_to_id = None
        mock_from_env.return_value = mock_config

        mock_commenter = MagicMock()
        mock_commenter_cls.return_value = mock_commenter

        main()

        # Bot comment must NOT trigger reactions or replies
        mock_commenter.add_reaction.assert_not_called()
        mock_commenter.reply_to_comment.assert_not_called()
        mock_commenter.post_issue_comment.assert_not_called()

    def test_extract_tommi_command_strict_prefixes(self):
        from src.main import extract_tommi_command

        # Valid commands
        self.assertEqual(extract_tommi_command("/tommi review"), ("review", ""))
        self.assertEqual(extract_tommi_command("/tommi help"), ("help", ""))
        self.assertEqual(extract_tommi_command("/tommi learn Never do X"), ("learn", "Never do X"))
        self.assertEqual(extract_tommi_command("/tommi false-positive Not a bug"), ("false-positive", "Not a bug"))
        self.assertEqual(extract_tommi_command("/tommi This is feedback", is_inline_reply=True), ("false-positive", "This is feedback"))

        # Ignored non-commands (URLs, mentions, help text lines, /review shortcut removed)
        self.assertIsNone(extract_tommi_command("https://github.com/thomasglasser/tommi/pull/9"))
        self.assertIsNone(extract_tommi_command("Please check thomasglasser/tommi repo for updates."))
        self.assertIsNone(extract_tommi_command("• /tommi review — Run automated review"))
        self.assertIsNone(extract_tommi_command("/review"))
        self.assertIsNone(extract_tommi_command("LGTM!"))

    @patch("src.main.GitHubCommenter")
    @patch("src.main.GitHubAuthManager")
    @patch("src.main.TommiConfig.from_env")
    def test_main_handles_github_auth_timeout_error(self, mock_from_env, mock_auth_cls, mock_commenter_cls):
        mock_config = MagicMock()
        mock_config.event_name = "pull_request_review_comment"
        mock_config.is_merged = False
        mock_config.comment_body = "/tommi false-positive variable shadowing"
        mock_config.comment_author = "thomasglasser"
        mock_config.comment_author_type = "User"
        mock_config.github_repository = "test/repo"
        mock_config.tommi_repo = "thomasglasser/tommi"
        mock_config.pr_number = 1
        mock_config.comment_id = 123
        mock_config.in_reply_to_id = 456
        mock_from_env.return_value = mock_config

        mock_commenter = MagicMock()
        mock_commenter_cls.return_value = mock_commenter

        mock_auth = MagicMock()
        # First call for target repo succeeds, second call for tommi_repo fails with 504 / auth error
        mock_auth.get_client_for_repo.side_effect = [
            MagicMock(),
            ValueError("No valid GitHub authentication available for repository 'thomasglasser/tommi': 504 Gateway Timeout")
        ]
        mock_auth_cls.return_value = mock_auth

        with self.assertRaises(SystemExit) as cm:
            main()

        self.assertEqual(cm.exception.code, 1)
        mock_commenter.reply_to_comment.assert_called_once()
        self.assertIn("Connection Error", mock_commenter.reply_to_comment.call_args[0][0])
        mock_commenter.add_reaction.assert_any_call("confused")


if __name__ == "__main__":
    unittest.main()
