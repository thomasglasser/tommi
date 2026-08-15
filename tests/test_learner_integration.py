import unittest
from unittest.mock import patch, MagicMock
from src.config import TommiConfig
from src.learner import TommiLearner

class TestLearnerIntegration(unittest.TestCase):
    def test_process_feedback_flow(self):
        config = TommiConfig(
            github_token="ghp_fake",
            gemini_api_key="fake_key",
            github_repository="test/repo",
            pr_number=10,
            model_name="auto"
        )

        mock_github = MagicMock()
        mock_tommi_repo = MagicMock()
        mock_tommi_repo.default_branch = "main"
        mock_tommi_repo.get_branch.return_value.commit.sha = "abcdef123456"

        mock_file_content = MagicMock()
        mock_file_content.decoded_content = b"# Core Rules\n\n## 1. Naming & Terminology\n* Existing rule\n"
        mock_file_content.sha = "filesha123"
        mock_tommi_repo.get_contents.return_value = mock_file_content

        mock_created_pr = MagicMock()
        mock_created_pr.html_url = "https://github.com/thomasglasser/tommi/pull/99"
        mock_created_pr.number = 99
        mock_tommi_repo.create_pull.return_value = mock_created_pr

        mock_github.get_repo.return_value = mock_tommi_repo

        with patch("src.learner.genai.Client") as mock_client_cls, \
             patch("src.learner.resolve_model_name", return_value="gemini-3.7-flash"):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            mock_gen_resp = MagicMock()
            mock_gen_resp.text = '{"target_file": "rules/core.md", "section_header": "## 1. Naming & Terminology", "rule_markdown": "* NEVER use abbreviation MLB.", "summary": "Do not abbreviate MLB", "rationale": "Clarity"}'
            mock_client.models.generate_content.return_value = mock_gen_resp

            learner = TommiLearner(config=config, github_client=mock_github)
            result = learner.process_feedback(
                command_type="learn",
                feedback_text="Never abbreviate MLB in variable names",
                pr_title="Add new feature",
                pr_diff="diff --git a/Test.java b/Test.java\n+ int MLB = 1;\n"
            )

            self.assertEqual(result["pr_url"], "https://github.com/thomasglasser/tommi/pull/99")
            self.assertEqual(result["learning_plan"]["summary"], "Do not abbreviate MLB")
            mock_tommi_repo.create_pull.assert_called_once()

    def test_learn_from_merged_pr_with_new_rules(self):
        config = TommiConfig(
            github_token="ghp_fake",
            gemini_api_key="fake_key",
            github_repository="test/repo",
            pr_number=10,
            event_name="pull_request",
            is_merged=True,
            model_name="auto"
        )

        mock_github = MagicMock()
        mock_tommi_repo = MagicMock()
        mock_tommi_repo.default_branch = "main"
        mock_tommi_repo.get_branch.return_value.commit.sha = "abcdef123456"

        mock_file_content = MagicMock()
        mock_file_content.decoded_content = b"# Minecraft Rules\n\n## 1. Modding Practices\n* Existing rule\n"
        mock_file_content.sha = "filesha123"
        mock_tommi_repo.get_contents.return_value = mock_file_content

        mock_created_pr = MagicMock()
        mock_created_pr.html_url = "https://github.com/thomasglasser/tommi/pull/100"
        mock_created_pr.number = 100
        mock_tommi_repo.create_pull.return_value = mock_created_pr
        mock_github.get_repo.return_value = mock_tommi_repo

        mock_pr = MagicMock()
        mock_pr.number = 10
        mock_pr.title = "Add new entity reversion"
        mock_pr.body = "Implements entity reversion"

        # Mock maintainer review comment
        mock_comment = MagicMock()
        mock_comment.user.login = "thomasglasser"
        mock_comment.user.type = "User"
        mock_comment.body = "Always use EntitySubPredicate instead of NbtPredicate for entity abilities."
        mock_comment.path = "src/Test.java"
        mock_comment.line = 42
        mock_comment.diff_hunk = "@@ -1 +1 @@"
        mock_pr.get_review_comments.return_value = [mock_comment]
        mock_pr.get_reviews.return_value = []
        mock_pr.get_issue_comments.return_value = []

        with patch("src.learner.genai.Client") as mock_client_cls, \
             patch("src.learner.resolve_model_name", return_value="gemini-3.7-flash"):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            mock_gen_resp = MagicMock()
            mock_gen_resp.text = '{"has_new_rules": true, "target_file": "rules/minecraft.md", "section_header": "## 1. Modding Practices", "rule_markdown": "* **Entity Predicates**: NEVER use NbtPredicate. ALWAYS implement EntitySubPredicate.", "summary": "Use EntitySubPredicate over NbtPredicate", "rationale": "Better performance", "source_comments": ["Always use EntitySubPredicate..."]}'
            mock_client.models.generate_content.return_value = mock_gen_resp

            learner = TommiLearner(config=config, github_client=mock_github)
            result = learner.learn_from_merged_pr(pr=mock_pr, pr_diff="diff --git a/Test.java\n")

            self.assertIsNotNone(result)
            self.assertEqual(result["pr_url"], "https://github.com/thomasglasser/tommi/pull/100")
            self.assertEqual(result["learning_plan"]["summary"], "Use EntitySubPredicate over NbtPredicate")

    def test_learn_from_merged_pr_no_human_comments(self):
        config = TommiConfig(
            github_token="ghp_fake",
            gemini_api_key="fake_key",
            github_repository="test/repo",
            pr_number=10,
            event_name="pull_request",
            is_merged=True
        )
        mock_pr = MagicMock()
        mock_pr.get_review_comments.return_value = []
        mock_pr.get_reviews.return_value = []
        mock_pr.get_issue_comments.return_value = []

        learner = TommiLearner(config=config, github_client=MagicMock())
        result = learner.learn_from_merged_pr(pr=mock_pr, pr_diff="diff")
        self.assertIsNone(result)

if __name__ == "__main__":
    unittest.main()
