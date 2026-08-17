import unittest
from unittest.mock import patch, MagicMock
from src.config import TommiConfig
from src.reviewer import TommiReviewer, HighDemandException, QuotaExceededException

class TestReviewerIntegration(unittest.TestCase):
    @patch("src.reviewer.requests.get")
    def test_review_pr_flow(self, mock_requests_get):
        # Mock PR diff response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """diff --git a/src/Test.java b/src/Test.java
index 1111111..2222222 100644
--- a/src/Test.java
+++ b/src/Test.java
@@ -1,3 +1,4 @@
 public class Test {
+    var x = 1;
 }
"""
        mock_requests_get.return_value = mock_resp

        config = TommiConfig(
            github_token="ghp_fake",
            gemini_api_key="fake_key",
            github_repository="test/repo",
            pr_number=1,
            model_name="auto"
        )

        with patch("src.reviewer.genai.Client") as mock_client_cls, \
             patch("src.reviewer.resolve_candidate_models", return_value=["gemini-3.7-flash"]):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            # Mock model response JSON
            mock_gen_response = MagicMock()
            mock_gen_response.text = '[{"path": "src/Test.java", "line": 2, "body": "Do not use var keyword."}]'
            mock_client.models.generate_content.return_value = mock_gen_response

            reviewer = TommiReviewer(config)
            comments = reviewer.review_pr("Test PR", "Test description", "https://api.github.com/repos/test/repo/pulls/1")

            self.assertEqual(len(comments), 1)
            self.assertEqual(comments[0]["path"], "src/Test.java")
            self.assertEqual(comments[0]["line"], 2)
            self.assertEqual(comments[0]["body"], "Do not use var keyword.")

    @patch("src.reviewer.requests.get")
    def test_review_pr_high_demand_fallback(self, mock_requests_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "diff --git a/src/Test.java b/src/Test.java\n+ var x = 1;\n"
        mock_requests_get.return_value = mock_resp

        config = TommiConfig(
            github_token="ghp_fake",
            gemini_api_key="fake_key",
            github_repository="test/repo",
            pr_number=1,
            model_name="auto"
        )

        with patch("src.reviewer.genai.Client") as mock_client_cls, \
             patch("src.reviewer.resolve_candidate_models", return_value=["gemini-3.7-flash", "gemini-2.0-flash"]):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            # First candidate throws 503 high demand, second succeeds
            mock_gen_response = MagicMock()
            mock_gen_response.text = '[]'
            mock_client.models.generate_content.side_effect = [
                Exception("503 UNAVAILABLE: This model is currently experiencing high demand."),
                mock_gen_response
            ]

            reviewer = TommiReviewer(config)
            comments = reviewer.review_pr("Test PR", "Test description", "https://api.github.com/repos/test/repo/pulls/1")
            self.assertEqual(comments, [])
            self.assertEqual(mock_client.models.generate_content.call_count, 2)

    @patch("src.reviewer.requests.get")
    def test_review_pr_all_high_demand(self, mock_requests_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "diff --git a/src/Test.java b/src/Test.java\n+ var x = 1;\n"
        mock_requests_get.return_value = mock_resp

        config = TommiConfig(
            github_token="ghp_fake",
            gemini_api_key="fake_key",
            github_repository="test/repo",
            pr_number=1,
            model_name="auto"
        )

        with patch("src.reviewer.genai.Client") as mock_client_cls, \
             patch("src.reviewer.resolve_candidate_models", return_value=["gemini-3.7-flash"]):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            mock_client.models.generate_content.side_effect = Exception("503 UNAVAILABLE: high demand")

            reviewer = TommiReviewer(config)
            with self.assertRaises(HighDemandException):
                reviewer.review_pr("Test PR", "Test description", "https://api.github.com/repos/test/repo/pulls/1")

    @patch("src.reviewer.requests.get")
    def test_review_pr_quota_exceeded(self, mock_requests_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "diff --git a/src/Test.java b/src/Test.java\n+ var x = 1;\n"
        mock_requests_get.return_value = mock_resp

        config = TommiConfig(
            github_token="ghp_fake",
            gemini_api_key="fake_key",
            github_repository="test/repo",
            pr_number=1,
            model_name="auto"
        )

        with patch("src.reviewer.genai.Client") as mock_client_cls, \
             patch("src.reviewer.resolve_candidate_models", return_value=["gemini-3.7-flash"]):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            mock_client.models.generate_content.side_effect = Exception("429 ResourceExhausted: Quota exceeded")

            reviewer = TommiReviewer(config)
            with self.assertRaises(QuotaExceededException):
                reviewer.review_pr("Test PR", "Test description", "https://api.github.com/repos/test/repo/pulls/1")

if __name__ == "__main__":
    unittest.main()

