import json
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
@@ -1,3 +1,12 @@
 public class Test {
+    var x = 1;
+    int a = 2;
+    int b = 3;
+    int c = 4;
+    int d = 5;
+    int e = 6;
+    int f = 7;
+    int g = 8;
+    int h = 9;
+    int i = 10;
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

            # Mock model response JSON with mixed severities returned in jumbled order
            mock_gen_response = MagicMock()
            mock_gen_response.text = json.dumps([
                {"path": "src/Test.java", "line": 10, "body": "Rename 'x' to 'counter' for clarity.", "severity": "SUGGESTION"},
                {"path": "src/Test.java", "line": 2, "body": "CRITICAL: Direct call to Minecraft.getInstance() in common code will crash dedicated server.", "severity": "CRITICAL"},
                {"path": "src/Test.java", "line": 5, "body": "Use ObjectArrayList instead of ArrayList.", "severity": "WARNING"}
            ])
            mock_client.models.generate_content.return_value = mock_gen_response

            reviewer = TommiReviewer(config)
            comments = reviewer.review_pr("Test PR", "Test description", "https://api.github.com/repos/test/repo/pulls/1")

            self.assertEqual(len(comments), 3)
            # CRITICAL should be first
            self.assertEqual(comments[0]["severity"], "CRITICAL")
            self.assertEqual(comments[0]["line"], 2)
            self.assertIn("crash dedicated server", comments[0]["body"])

            # WARNING should be second
            self.assertEqual(comments[1]["severity"], "WARNING")
            self.assertEqual(comments[1]["line"], 5)
            self.assertIn("ObjectArrayList", comments[1]["body"])

            # SUGGESTION should be last
            self.assertEqual(comments[2]["severity"], "SUGGESTION")
            self.assertEqual(comments[2]["line"], 10)
            self.assertIn("Rename 'x'", comments[2]["body"])

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
             patch("src.reviewer.resolve_candidate_models", return_value=["gemini-3.7-flash", "gemini-3.6-flash"]), \
             patch("src.reviewer.time.sleep") as mock_sleep:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            # First candidate throws 503 high demand (2 attempts with backoff), second succeeds
            mock_gen_response = MagicMock()
            mock_gen_response.text = '[]'
            mock_client.models.generate_content.side_effect = [
                Exception("503 UNAVAILABLE: This model is currently experiencing high demand."),
                Exception("503 UNAVAILABLE: This model is currently experiencing high demand."),
                mock_gen_response
            ]

            reviewer = TommiReviewer(config)
            comments = reviewer.review_pr("Test PR", "Test description", "https://api.github.com/repos/test/repo/pulls/1")
            self.assertEqual(comments, [])
            self.assertEqual(mock_client.models.generate_content.call_count, 3)
            self.assertTrue(mock_sleep.called)

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
             patch("src.reviewer.resolve_candidate_models", return_value=["gemini-3.7-flash"]), \
             patch("src.reviewer.time.sleep"):
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
             patch("src.reviewer.resolve_candidate_models", return_value=["gemini-3.7-flash"]), \
             patch("src.reviewer.time.sleep"):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            mock_client.models.generate_content.side_effect = Exception("429 ResourceExhausted: Quota exceeded")

            reviewer = TommiReviewer(config)
            with self.assertRaises(QuotaExceededException):
                reviewer.review_pr("Test PR", "Test description", "https://api.github.com/repos/test/repo/pulls/1")

    def test_validate_comments_sorting(self):
        from src.diff_parser import parse_unified_diff
        diff = parse_unified_diff("diff --git a/Test.java b/Test.java\n@@ -1,5 +1,5 @@\n+line1\n+line2\n+line3\n+line4\n+line5\n")
        config = TommiConfig(gemini_api_key="key", github_repository="owner/repo", pr_number=1)
        with patch("src.reviewer.genai.Client"):
            reviewer = TommiReviewer(config)

        raw = [
            {"path": "Test.java", "line": 5, "body": "nitpick 1", "severity": "SUGGESTION"},
            {"path": "Test.java", "line": 1, "body": "critical crash", "severity": "CRITICAL"},
            {"path": "Test.java", "line": 3, "body": "warning api", "severity": "WARNING"},
            {"path": "Test.java", "line": 2, "body": "warning unstated severity"},
            {"path": "Test.java", "line": 4, "body": "nitpick 2", "severity": "SUGGESTION"},
        ]
        validated = reviewer._validate_comments(raw, diff)
        self.assertEqual(len(validated), 5)
        self.assertEqual(validated[0]["severity"], "CRITICAL")
        self.assertEqual(validated[0]["body"], "critical crash")
        self.assertEqual(validated[1]["severity"], "WARNING")
        self.assertEqual(validated[1]["body"], "warning api")
        self.assertEqual(validated[2]["severity"], "WARNING")
        self.assertEqual(validated[2]["body"], "warning unstated severity")
        self.assertEqual(validated[3]["severity"], "SUGGESTION")
        self.assertEqual(validated[3]["body"], "nitpick 1")
        self.assertEqual(validated[4]["severity"], "SUGGESTION")
        self.assertEqual(validated[4]["body"], "nitpick 2")

    def test_parse_and_repair_json_clean(self):
        config = TommiConfig(gemini_api_key="key", github_repository="owner/repo", pr_number=1)
        with patch("src.reviewer.genai.Client"):
            reviewer = TommiReviewer(config)

        res = reviewer._parse_and_repair_json('[{"path": "Test.java", "line": 1, "body": "ok", "severity": "WARNING"}]')
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["path"], "Test.java")

    def test_parse_and_repair_json_fences_and_wrappers(self):
        config = TommiConfig(gemini_api_key="key", github_repository="owner/repo", pr_number=1)
        with patch("src.reviewer.genai.Client"):
            reviewer = TommiReviewer(config)

        # Markdown fences
        res = reviewer._parse_and_repair_json('```json\n[{"path": "Test.java", "line": 1, "body": "ok"}]\n```')
        self.assertEqual(len(res), 1)

        # Dict with 'comments' key
        res = reviewer._parse_and_repair_json('{"comments": [{"path": "Test.java", "line": 1, "body": "ok"}]}')
        self.assertEqual(len(res), 1)

        # Single dict
        res = reviewer._parse_and_repair_json('{"path": "Test.java", "line": 1, "body": "ok"}')
        self.assertEqual(len(res), 1)

    def test_parse_and_repair_json_truncated_salvage(self):
        config = TommiConfig(gemini_api_key="key", github_repository="owner/repo", pr_number=1)
        with patch("src.reviewer.genai.Client"):
            reviewer = TommiReviewer(config)

        # Truncated JSON array where 2nd object is cut off mid-string
        truncated_json = """[
          {"path": "Test.java", "line": 1, "body": "First issue", "severity": "CRITICAL"},
          {"path": "Test2.java", "line": 5, "body": "Unterminated string starting at line 18 colu"""
        res = reviewer._parse_and_repair_json(truncated_json)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["path"], "Test.java")
        self.assertEqual(res[0]["body"], "First issue")

    @patch("src.reviewer.requests.get")
    def test_review_pr_fallback_on_parse_error(self, mock_requests_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "diff --git a/src/Test.java b/src/Test.java\n@@ -1,3 +1,3 @@\n+line1\n"
        mock_requests_get.return_value = mock_resp

        config = TommiConfig(
            github_token="ghp_fake",
            gemini_api_key="fake_key",
            github_repository="test/repo",
            pr_number=1,
            model_name="auto"
        )

        with patch("src.reviewer.genai.Client") as mock_client_cls, \
             patch("src.reviewer.resolve_candidate_models", return_value=["gemini-3.7-flash", "gemini-3.6-flash"]):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            # First candidate returns unparseable garbage on both attempts, second returns valid JSON
            resp1 = MagicMock()
            resp1.text = "This is total garbage not JSON at all."
            resp2 = MagicMock()
            resp2.text = '[{"path": "src/Test.java", "line": 1, "body": "Fixed", "severity": "WARNING"}]'

            mock_client.models.generate_content.side_effect = [resp1, resp1, resp2]

            reviewer = TommiReviewer(config)
            comments = reviewer.review_pr("Test PR", "Test description", "https://api.github.com/repos/test/repo/pulls/1")
            self.assertEqual(len(comments), 1)
            self.assertEqual(comments[0]["body"], "Fixed")
            self.assertEqual(mock_client.models.generate_content.call_count, 3)

    @patch("src.reviewer.requests.get")
    def test_review_pr_fallback_on_schema_error(self, mock_requests_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "diff --git a/src/Test.java b/src/Test.java\n@@ -1,3 +1,3 @@\n+line1\n"
        mock_requests_get.return_value = mock_resp

        config = TommiConfig(
            github_token="ghp_fake",
            gemini_api_key="fake_key",
            github_repository="test/repo",
            pr_number=1,
            model_name="auto"
        )

        with patch("src.reviewer.genai.Client") as mock_client_cls, \
             patch("src.reviewer.resolve_candidate_models", return_value=["gemini-3.7-flash", "gemini-3.6-flash"]):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            # First attempt throws Unsupported schema type, retries on same model and succeeds
            resp2 = MagicMock()
            resp2.text = '[{"path": "src/Test.java", "line": 1, "body": "Fixed after schema fallback", "severity": "WARNING"}]'

            mock_client.models.generate_content.side_effect = [
                ValueError("Unsupported schema type"),
                resp2
            ]
            reviewer = TommiReviewer(config)
            comments = reviewer.review_pr("Test PR", "Test description", "https://api.github.com/repos/test/repo/pulls/1")
            self.assertEqual(len(comments), 1)
            self.assertEqual(comments[0]["body"], "Fixed after schema fallback")
            self.assertEqual(mock_client.models.generate_content.call_count, 2)

    def test_real_genai_schema_serialization(self):
        """
        Validates that ReviewCommentItem produces a valid schema object
        when processed by google.genai's real transformer without mocking.
        """
        from google import genai
        from google.genai import types
        from google.genai.models import _GenerateContentConfig_to_mldev
        from src.reviewer import ReviewCommentItem

        real_client = genai.Client(api_key="fake_key")
        cfg = types.GenerateContentConfig(
            temperature=0.15,
            response_mime_type="application/json",
            response_schema=list[ReviewCommentItem],
            max_output_tokens=65536,
        )

        transformed = _GenerateContentConfig_to_mldev(real_client._api_client, cfg)
        self.assertIn("responseSchema", transformed)
        self.assertEqual(transformed["responseSchema"].type.value, "ARRAY")
        self.assertIn("path", transformed["responseSchema"].items.properties)
        self.assertIn("line", transformed["responseSchema"].items.properties)
        self.assertIn("body", transformed["responseSchema"].items.properties)


    @patch("src.reviewer.requests.get")
    def test_review_pr_with_workspace_tool_call(self, mock_requests_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "diff --git a/src/Test.java b/src/Test.java\n+ var x = MyManager.get(player);\n"
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

            # Turn 1: Model calls search_codebase tool
            fc = MagicMock()
            fc.name = "search_codebase"
            fc.args = {"query": "MyManager"}

            part = MagicMock()
            part.function_call = fc

            candidate = MagicMock()
            candidate.content.parts = [part]

            resp_turn1 = MagicMock()
            resp_turn1.function_calls = [fc]
            resp_turn1.candidates = [candidate]

            # Turn 2: Model returns final review after receiving tool response
            resp_turn2 = MagicMock()
            resp_turn2.function_calls = None
            resp_turn2.candidates = []
            resp_turn2.text = '[{"path": "src/Test.java", "line": 1, "body": "Do not use var", "severity": "WARNING"}]'

            mock_client.models.generate_content.side_effect = [resp_turn1, resp_turn2]

            reviewer = TommiReviewer(config)
            comments = reviewer.review_pr("Test PR", "Test description", "https://api.github.com/repos/test/repo/pulls/1")

            self.assertEqual(len(comments), 1)
            self.assertEqual(comments[0]["body"], "Do not use var")
            self.assertEqual(mock_client.models.generate_content.call_count, 2)

    @patch("src.reviewer.requests.get")
    def test_tool_error_503_raises_high_demand_and_never_claims_zero_violations(self, mock_requests_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "diff --git a/src/Test.java b/src/Test.java\n+ var x = MyManager.get(player);\n"
        mock_requests_get.return_value = mock_resp

        config = TommiConfig(
            github_token="ghp_fake",
            gemini_api_key="fake_key",
            github_repository="test/repo",
            pr_number=1,
            model_name="auto"
        )

        with patch("src.reviewer.genai.Client") as mock_client_cls, \
             patch("src.reviewer.resolve_candidate_models", return_value=["gemini-3.7-flash"]), \
             patch("src.reviewer.time.sleep"):
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            # Turn 1: Model requests a tool
            fc = MagicMock()
            fc.name = "search_codebase"
            fc.args = {"query": "MyManager"}
            part = MagicMock()
            part.function_call = fc
            candidate = MagicMock()
            candidate.content.parts = [part]
            resp_turn1 = MagicMock()
            resp_turn1.function_calls = [fc]
            resp_turn1.candidates = [candidate]

            # Turn 2: Attempting to send tool response throws 503 UNAVAILABLE
            mock_client.models.generate_content.side_effect = [
                resp_turn1,
                Exception("503 UNAVAILABLE: This model is currently experiencing high demand."),
                Exception("503 UNAVAILABLE: This model is currently experiencing high demand.")
            ]

            reviewer = TommiReviewer(config)
            # MUST raise HighDemandException rather than returning [] or masking the error
            with self.assertRaises(HighDemandException):
                reviewer.review_pr("Test PR", "Test description", "https://api.github.com/repos/test/repo/pulls/1")

    @patch("src.reviewer.requests.get")
    def test_tool_budget_exhaustion_synthesizes_final_turn(self, mock_requests_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "diff --git a/src/Test.java b/src/Test.java\n+ int x = 1;\n"
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

            fc = MagicMock()
            fc.name = "find_files"
            fc.args = {"pattern": "*.java"}
            part = MagicMock()
            part.function_call = fc
            candidate = MagicMock()
            candidate.content.parts = [part]
            resp_tool_turn = MagicMock()
            resp_tool_turn.function_calls = [fc]
            resp_tool_turn.candidates = [candidate]

            final_resp = MagicMock()
            final_resp.function_calls = None
            final_resp.candidates = []
            final_resp.text = '[{"path": "src/Test.java", "line": 1, "body": "Synthesized after 3 tool turns", "severity": "SUGGESTION"}]'

            # 3 tool turns + 1 final synthesis turn = 4 generate_content calls
            mock_client.models.generate_content.side_effect = [
                resp_tool_turn,
                resp_tool_turn,
                resp_tool_turn,
                final_resp
            ]

            reviewer = TommiReviewer(config)
            comments = reviewer.review_pr("Test PR", "Test description", "https://api.github.com/repos/test/repo/pulls/1")
            self.assertEqual(len(comments), 1)
            self.assertEqual(comments[0]["body"], "Synthesized after 3 tool turns")
            self.assertEqual(mock_client.models.generate_content.call_count, 4)


if __name__ == "__main__":
    unittest.main()



