import unittest
from unittest.mock import patch, MagicMock
from src.config import TommiConfig
from src.main import extract_tommi_command
from src.chat import TommiConversationHandler


class TestTommiChat(unittest.TestCase):
    def test_extract_tommi_command_slash_commands(self):
        self.assertEqual(extract_tommi_command("/tommi review"), ("review", ""))
        self.assertEqual(extract_tommi_command("/tommi help"), ("help", ""))
        self.assertEqual(extract_tommi_command("/tommi learn ALWAYS use X"), ("learn", "ALWAYS use X"))
        self.assertEqual(extract_tommi_command("/tommi false-positive This is safe"), ("false-positive", "This is safe"))
        self.assertEqual(extract_tommi_command("/tommi foobar"), ("unrecognized", "/tommi foobar"))

    def test_extract_tommi_command_discussion_pings(self):
        self.assertEqual(
            extract_tommi_command("@tommi why did you flag this?"),
            ("discuss", "@tommi why did you flag this?")
        )
        self.assertEqual(
            extract_tommi_command("Hey @t-o-m-m-i-ai-reviewer, can you take another look?"),
            ("discuss", "Hey @t-o-m-m-i-ai-reviewer, can you take another look?")
        )
        self.assertEqual(
            extract_tommi_command("This is safe because X. What do you think @tommi?"),
            ("discuss", "This is safe because X. What do you think @tommi?")
        )

    def test_extract_tommi_command_no_invocation(self):
        self.assertIsNone(extract_tommi_command("Looks good to me!"))
        self.assertIsNone(extract_tommi_command("Thanks for the review."))
        self.assertIsNone(extract_tommi_command(""))
        self.assertIsNone(extract_tommi_command(None))

    @patch("src.chat.genai.Client")
    @patch("src.chat.resolve_candidate_models", return_value=["gemini-3.8-flash"])
    def test_handle_discussion_generates_reply(self, mock_resolve, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_gen_resp = MagicMock()
        mock_gen_resp.text = "Thanks for clarifying! Since this runs on the client side, it is safe."
        mock_client.models.generate_content.return_value = mock_gen_resp

        config = TommiConfig(
            github_token="fake_token",
            gemini_api_key="fake_key",
            github_repository="test/repo",
            pr_number=1,
        )

        mock_pr = MagicMock()
        mock_pr.number = 1
        mock_pr.title = "Test PR"

        mock_root_comment = MagicMock()
        mock_root_comment.id = 100
        mock_root_comment.user.login = "t-o-m-m-i-ai-reviewer"
        mock_root_comment.body = "Direct call to Minecraft.getInstance() may crash server."
        mock_pr.get_comment.return_value = mock_root_comment
        mock_pr.get_review_comments.return_value = []

        handler = TommiConversationHandler(config=config, github_client=MagicMock())
        response = handler.handle_discussion(
            pr=mock_pr,
            comment_body="@tommi this is inside a clientbound packet handler so it's client-only",
            comment_author="contributor",
            in_reply_to_id=100,
            file_path="src/ClientPayload.java",
            diff_hunk="@@ -1,3 +1,4 @@",
        )

        self.assertIn("safe", response)
        mock_client.models.generate_content.assert_called_once()


if __name__ == "__main__":
    unittest.main()
