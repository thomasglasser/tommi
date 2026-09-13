import os
import shutil
import subprocess
import unittest
from unittest.mock import MagicMock
import requests

from src.diff_parser import parse_unified_diff, filter_diff_for_review
from src.reviewer import TommiReviewer
from src.commenter import GitHubCommenter
from src.config import TommiConfig

CORE_EVENTS_DIFF_HUNK = """diff --git a/src/main/java/dev/thomasglasser/mineraculous/impl/core/MineraculousCoreEvents.java b/src/main/java/dev/thomasglasser/mineraculous/impl/core/MineraculousCoreEvents.java
index 281a812..039bbf8 100644
--- a/src/main/java/dev/thomasglasser/mineraculous/impl/core/MineraculousCoreEvents.java
+++ b/src/main/java/dev/thomasglasser/mineraculous/impl/core/MineraculousCoreEvents.java
@@ -10,6 +10,7 @@ package dev.thomasglasser.mineraculous.impl.core;
 import net.minecraft.world.entity.player.Player;
 import net.minecraft.server.level.ServerLevel;
+import java.util.UUID;
 
 public class MineraculousCoreEvents {
@@ -212,6 +213,8 @@ public class MineraculousCoreEvents {
     public static void onPlayerTick(Player player) {
+        // Tick kwamis
+        KwamiUtils.tickKwamis(player);
     }
@@ -316,7 +319,8 @@ public class MineraculousCoreEvents {
         ServerLevel level = player.serverLevel();
         for (UUID clearedId : beforeIds) {
             if (!afterIds.contains(clearedId)) {
-                KwamiUtils.discardKwami(level, clearedId);
+                KwamiUtils.discardKwami(level, null, clearedId);
             }
         }
     }
"""


class TestLiveDiffSearch(unittest.TestCase):
    def test_real_pr87_core_events_hunk_offline(self):
        """
        Offline test validating line searching and snapping on the exact diff hunk
        from MineraculousCoreEvents.java that triggered the PR #87 batch failure.
        """
        parsed = parse_unified_diff(CORE_EVENTS_DIFF_HUNK)
        path = "src/main/java/dev/thomasglasser/mineraculous/impl/core/MineraculousCoreEvents.java"

        self.assertIn(path, parsed.files)
        # Line 293 is not in diff
        self.assertFalse(parsed.is_line_in_diff(path, 293))

        # Snapping with max_distance=3 failed previously (distance to 316 is 23)
        self.assertIsNone(parsed.get_closest_valid_line(path, 293, max_distance=3))

        # Snapping with default max_distance=30 successfully snaps to 319
        snapped = parsed.get_closest_valid_line(path, 293)
        self.assertEqual(snapped, 319)

        # Code search finds line 319
        matched = parsed.find_matching_line(path, "ServerLevel level = player.serverLevel();", preferred_line=293)
        self.assertEqual(matched, 319)

        # Line 150 is far away (>30 lines) -> should return None
        self.assertIsNone(parsed.get_closest_valid_line(path, 150))

        # Test validation and batch review structuring
        config = TommiConfig(gemini_api_key="fake", github_repository="owner/repo", pr_number=87)
        reviewer = TommiReviewer(config)

        raw_comments = [
            {"path": path, "line": 293, "body": "Issue near 293", "severity": "WARNING"},
            {"path": path, "line": 150, "body": "Far off-diff issue", "severity": "SUGGESTION"},
        ]
        validated = reviewer._validate_comments(raw_comments, parsed)
        self.assertEqual(len(validated), 2)

        # Line 293 snapped to 319 and is valid
        self.assertEqual(validated[0]["line"], 319)
        self.assertTrue(validated[0]["is_valid_line"])

        # Line 150 could not snap and is marked off-diff
        self.assertFalse(validated[1]["is_valid_line"])

        # Simulate commenter
        mock_github = MagicMock()
        mock_repo = MagicMock()
        mock_pr = MagicMock()
        mock_github.get_repo.return_value = mock_repo
        mock_repo.get_pull.return_value = mock_pr
        mock_pr.get_commits.return_value = [MagicMock()]

        commenter = GitHubCommenter(mock_github, "owner/repo", 87)
        commenter.post_review_comments(validated)

        # Batch review called once with ONLY line 319
        mock_pr.create_review.assert_called_once()
        kwargs = mock_pr.create_review.call_args[1]
        batch_comments = kwargs["comments"]
        self.assertEqual(len(batch_comments), 1)
        self.assertEqual(batch_comments[0]["line"], 319)
        self.assertIn("Far off-diff issue", kwargs["body"])

    def test_live_api_diff_search_and_batch_validation(self):
        """
        Live integration test: Queries the GitHub API for PR #87 diff, searches and parses it,
        and verifies that 100% of batch comments are valid diff lines without modifying GitHub.
        """
        token = os.environ.get("GITHUB_TOKEN")
        if not token and shutil.which("gh"):
            try:
                res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
                if res.returncode == 0:
                    token = res.stdout.strip()
            except Exception:
                pass

        if not token:
            self.skipTest("No GitHub token or 'gh' CLI available for live API test.")

        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3.diff",
        }

        try:
            resp = requests.get(
                "https://api.github.com/repos/Mineraculous/Mineraculous/pulls/87",
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                # Fallback to discovering the latest open PR in the repo
                open_prs = requests.get(
                    "https://api.github.com/repos/Mineraculous/Mineraculous/pulls?state=open&per_page=1",
                    headers={"Authorization": f"token {token}"},
                    timeout=10,
                ).json()
                if open_prs:
                    pr_num = open_prs[0]["number"]
                    resp = requests.get(
                        f"https://api.github.com/repos/Mineraculous/Mineraculous/pulls/{pr_num}",
                        headers=headers,
                        timeout=15,
                    )
        except Exception as e:
            self.skipTest(f"Live GitHub API unreachable: {e}")

        if resp.status_code != 200:
            self.skipTest(f"GitHub API returned HTTP {resp.status_code}")

        raw_diff = resp.text
        filtered_diff = filter_diff_for_review(raw_diff)
        parsed_diff = parse_unified_diff(filtered_diff)

        # Verify real diff was parsed
        self.assertGreater(len(parsed_diff.files), 50)
        core_events_path = "src/main/java/dev/thomasglasser/mineraculous/impl/core/MineraculousCoreEvents.java"
        self.assertIn(core_events_path, parsed_diff.files)

        # Verify line 293 snaps to 316 in live diff
        snapped = parsed_diff.get_closest_valid_line(core_events_path, 293)
        self.assertEqual(snapped, 316)

        # Verify diff searching for snippet
        matched = parsed_diff.find_matching_line(core_events_path, "ServerLevel level = player.serverLevel();", preferred_line=290)
        self.assertEqual(matched, 316)

        # Test full commenter pipeline with mock PR
        config = TommiConfig(gemini_api_key="fake", github_repository="Mineraculous/Mineraculous", pr_number=87)
        reviewer = TommiReviewer(config)

        test_comments = [
            {"path": core_events_path, "line": 293, "body": "Check kwami discard logic", "severity": "WARNING"},
            {"path": core_events_path, "line": 9999, "body": "Nonexistent line note", "severity": "SUGGESTION"},
        ]
        validated = reviewer._validate_comments(test_comments, parsed_diff)

        mock_github = MagicMock()
        mock_repo = MagicMock()
        mock_pr = MagicMock()
        mock_github.get_repo.return_value = mock_repo
        mock_repo.get_pull.return_value = mock_pr
        mock_pr.get_commits.return_value = [MagicMock()]

        commenter = GitHubCommenter(mock_github, "Mineraculous/Mineraculous", 87)
        commenter.post_review_comments(validated)

        # Ensure no live comments were posted
        mock_pr.create_review_comment.assert_not_called()
        mock_pr.create_issue_comment.assert_not_called()

        # Ensure create_review was called with strictly valid diff lines
        mock_pr.create_review.assert_called_once()
        kwargs = mock_pr.create_review.call_args[1]
        for c in kwargs["comments"]:
            self.assertTrue(parsed_diff.is_line_in_diff(c["path"], c["line"]))

        # Ensure off-diff comment is in the body
        self.assertIn("Nonexistent line note", kwargs["body"])

    def test_live_gemini_review_generation_if_key_available(self):
        """
        Live AI test: If GEMINI_API_KEY is available in the environment, queries the actual
        Gemini API with a real file diff from an open PR to verify high-reasoning review generation.
        """
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            self.skipTest("GEMINI_API_KEY not set in local environment (skipping live AI inference).")

        config = TommiConfig(gemini_api_key=api_key, github_repository="Mineraculous/Mineraculous", pr_number=87)
        reviewer = TommiReviewer(config)

        # Real Java diff snippet from Mineraculous
        test_diff = (
            "diff --git a/src/main/java/dev/thomasglasser/mineraculous/impl/Test.java b/src/main/java/dev/thomasglasser/mineraculous/impl/Test.java\n"
            "--- a/src/main/java/dev/thomasglasser/mineraculous/impl/Test.java\n"
            "+++ b/src/main/java/dev/thomasglasser/mineraculous/impl/Test.java\n"
            "@@ -1,3 +1,5 @@\n"
            " public class Test {\n"
            "+    public static java.util.ArrayList<String> list = new java.util.ArrayList<>();\n"
            "+    public void tick() { java.util.UUID id = java.util.UUID.randomUUID(); }\n"
            " }\n"
        )
        parsed = parse_unified_diff(test_diff)
        rules = TommiConfig(gemini_api_key=api_key, github_repository="owner/repo", pr_number=1)
        from src.rules_loader import load_all_rules
        all_rules = load_all_rules()

        prompt = reviewer._build_review_prompt("Test PR", "Testing high reasoning", test_diff, all_rules, parsed_diff=parsed)
        response_text = reviewer._execute_review_generation("gemini-2.5-flash", prompt, enable_tools=False)
        self.assertIsNotNone(response_text)
        comments = reviewer._parse_and_repair_json(response_text)
        self.assertIsInstance(comments, list)


if __name__ == "__main__":
    unittest.main()
