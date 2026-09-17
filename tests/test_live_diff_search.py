import logging
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("tommi.live_test")

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
        logger.info("=" * 70)
        logger.info("STARTING OFFLINE VERIFICATION: PR #87 MineraculousCoreEvents.java Hunk")
        logger.info("=" * 70)

        parsed = parse_unified_diff(CORE_EVENTS_DIFF_HUNK)
        path = "src/main/java/dev/thomasglasser/mineraculous/impl/core/MineraculousCoreEvents.java"

        self.assertIn(path, parsed.files)
        valid_lines = sorted(parsed.files[path])
        logger.info(f"Parsed hunk for '{path}': {len(valid_lines)} valid diff lines: {valid_lines}")

        # Line 293 is not in diff
        self.assertFalse(parsed.is_line_in_diff(path, 293))
        logger.info("Verified: Line 293 is NOT in diff (falls in gap between hunks 213 and 319).")

        # Snapping with default max_distance=3 returns None (distance to 319 is 26)
        default_snapped = parsed.get_closest_valid_line(path, 293)
        self.assertIsNone(default_snapped)
        logger.info(f"Verified: Line 293 with default max_distance=3 correctly returns None (tightened snapping).")

        # Snapping with explicit max_distance=30 can still snap to 319 if requested
        snapped_30 = parsed.get_closest_valid_line(path, 293, max_distance=30)
        self.assertEqual(snapped_30, 319)
        logger.info(f"Verified: Line 293 with max_distance=30 successfully snapped to line {snapped_30} in active hunk.")

        # Code search finds line 319
        matched = parsed.find_matching_line(path, "ServerLevel level = player.serverLevel();", preferred_line=293)
        self.assertEqual(matched, 319)
        logger.info(f"Verified: Code content search matched 'ServerLevel level = player.serverLevel();' to line {matched}.")

        # Line 150 is far away (>30 lines) -> should return None
        far_snapped = parsed.get_closest_valid_line(path, 150)
        self.assertIsNone(far_snapped)
        logger.info(f"Verified: Line 150 (>30 lines from any hunk) correctly rejected: {far_snapped}.")

        # Test validation and batch review structuring
        config = TommiConfig(gemini_api_key="fake", github_repository="owner/repo", pr_number=87)
        reviewer = TommiReviewer(config)

        raw_comments = [
            {"path": path, "line": 293, "target_code": "ServerLevel level = player.serverLevel();", "body": "Issue near 293", "severity": "WARNING"},
            {"path": path, "line": 150, "body": "Far off-diff issue", "severity": "SUGGESTION"},
        ]
        validated = reviewer._validate_comments(raw_comments, parsed)
        self.assertEqual(len(validated), 2)
        logger.info(f"Validated comments: {len(validated)} processed:")
        for c in validated:
            logger.info(f" - {c['path']}:{c['line']} (is_valid_line={c.get('is_valid_line')}) -> {c['body']}")

        # Line 293 matched target_code to 319 and is valid
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
        logger.info(f"Verified batch review payload: {len(batch_comments)} inline comment(s), off-diff notes in body.")
        logger.info("OFFLINE VERIFICATION PASSED.\n")

    def test_live_api_diff_search_and_batch_validation(self):
        """
        Live integration test: Queries the GitHub API for the latest open PR diff,
        searches and parses it, and verifies that 100% of batch comments are valid diff lines
        without modifying GitHub.
        """
        logger.info("=" * 70)
        logger.info("LIVE TEST STEP 1: RESOLVING GITHUB AUTH TOKEN")
        logger.info("=" * 70)

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

        logger.info("GitHub authentication token acquired via 'gh auth token'.")

        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3.diff",
        }

        logger.info("=" * 70)
        logger.info("LIVE TEST STEP 2: DISCOVERING LATEST OPEN PR IN MINERACULOUS")
        logger.info("=" * 70)

        # 1. Discover the latest open PR dynamically from GitHub
        try:
            prs_resp = requests.get(
                "https://api.github.com/repos/Mineraculous/Mineraculous/pulls?state=open&sort=created&direction=desc&per_page=3",
                headers={"Authorization": f"token {token}"},
                timeout=10,
            )
            if prs_resp.status_code != 200 or not prs_resp.json():
                self.skipTest("No open PRs found in Mineraculous repository.")

            open_prs = prs_resp.json()
            logger.info(f"Discovered {len(open_prs)} open PR(s) in Mineraculous/Mineraculous:")
            for p in open_prs:
                logger.info(f" - PR #{p['number']}: '{p['title']}' (branch: {p['head']['ref']}, state: {p['state']}, draft: {p.get('draft')})")

        except Exception as e:
            self.skipTest(f"Live GitHub API unreachable: {e}")

        # Test each discovered open PR (up to 3 PRs: e.g. PR #91, #88, #87)
        for pr_idx, pr_data in enumerate(open_prs[:3]):
            pr_number = pr_data["number"]
            pr_title = pr_data["title"]
            pr_diff_url = pr_data["url"]
            pr_branch = pr_data["head"]["ref"]

            logger.info("=" * 70)
            logger.info(f"TESTING PR [{pr_idx + 1}/{min(3, len(open_prs))}]: PR #{pr_number} '{pr_title}' (branch: {pr_branch})")
            logger.info("=" * 70)
            logger.info(f"Fetching live unified diff from: {pr_diff_url}")

            try:
                resp = requests.get(pr_diff_url, headers=headers, timeout=15)
            except Exception as req_err:
                logger.warning(f"Could not fetch diff for PR #{pr_number}: {req_err}")
                continue

            if resp.status_code != 200:
                logger.warning(f"GitHub API returned HTTP {resp.status_code} for PR #{pr_number}")
                continue

            raw_diff = resp.text
            logger.info(f"Successfully fetched raw unified diff: {len(raw_diff):,} characters across {raw_diff.count('diff --git ')} total files.")

            logger.info(f"[PR #{pr_number}] Step 1: Filtering code files & parsing unified diff...")
            filtered_diff = filter_diff_for_review(raw_diff)
            code_file_count = filtered_diff.count('diff --git ')
            total_file_count = raw_diff.count('diff --git ')
            logger.info(f"Filtered out {total_file_count - code_file_count} non-code/asset/resource file(s).")
            logger.info(f"Retained {code_file_count} reviewable executable code file(s) ({len(filtered_diff):,} characters).")

            parsed_diff = parse_unified_diff(filtered_diff)
            logger.info(f"Parsed diff structure: {len(parsed_diff.files)} unique code file(s) mapped with valid diff lines.")

            self.assertGreater(len(parsed_diff.files), 0, f"PR #{pr_number} ({pr_title}) should have at least 1 reviewable code file")

            logger.info(f"[PR #{pr_number}] Step 2: Deep inspection of modified code files & line mappings...")
            inspected_count = 0
            sample_file = None
            sample_first_valid_line = None

            for file_path, lines_set in list(parsed_diff.files.items())[:3]:
                sorted_lines = sorted(lines_set)
                pos_lines = [l for l in sorted_lines if l > 0]
                if not pos_lines:
                    continue

                logger.info(f"File [{inspected_count + 1}]: {file_path}")
                logger.info(f"  Total valid diff lines: {len(pos_lines):,} (range: {pos_lines[0]} to {pos_lines[-1]})")

                if sample_file is None:
                    sample_file = file_path
                    sample_first_valid_line = pos_lines[0]

                # Verify individual line checks
                test_line = pos_lines[0]
                self.assertTrue(parsed_diff.is_line_in_diff(file_path, test_line))
                logger.info(f"  Verified line {test_line} is in diff: True")

                # Test snapping near the hunk
                target_near = max(1, test_line - 2)
                snapped_near = parsed_diff.get_closest_valid_line(file_path, target_near, max_distance=30)
                logger.info(f"  Snapping test: target={target_near} -> snapped={snapped_near} (valid: {parsed_diff.is_line_in_diff(file_path, snapped_near) if snapped_near else False})")

                # Test code search if content exists
                if file_path in parsed_diff.line_contents and test_line in parsed_diff.line_contents[file_path]:
                    snippet = parsed_diff.line_contents[file_path][test_line].strip()
                    if snippet:
                        matched_line = parsed_diff.find_matching_line(file_path, snippet, preferred_line=target_near)
                        logger.info(f"  Code content match test: '{snippet[:50]}...' -> matched line {matched_line}")
                        self.assertIsNotNone(matched_line)
                        self.assertTrue(parsed_diff.is_line_in_diff(file_path, matched_line))

                inspected_count += 1

            self.assertIsNotNone(sample_file, f"Could not find a reviewable file with positive line numbers in PR #{pr_number}.")
            self.assertIsNotNone(sample_first_valid_line, f"Could not find a valid line number in PR #{pr_number}.")

            logger.info(f"[PR #{pr_number}] Step 3: Validating comments & preventing off-diff batch failures...")
            config = TommiConfig(gemini_api_key="fake", github_repository="Mineraculous/Mineraculous", pr_number=pr_number)
            reviewer = TommiReviewer(config)

            target_near_hunk = max(1, sample_first_valid_line - 2)
            test_comments = [
                {"path": sample_file, "line": sample_first_valid_line, "body": f"Exact line comment on {sample_file}", "severity": "CRITICAL"},
                {"path": sample_file, "line": target_near_hunk, "body": f"Near-hunk comment on {sample_file}", "severity": "WARNING"},
                {"path": sample_file, "line": 999999, "body": f"Off-diff architectural note for {sample_file}", "severity": "SUGGESTION"},
            ]

            validated = reviewer._validate_comments(test_comments, parsed_diff)
            logger.info(f"Validation output for PR #{pr_number} ({len(validated)} comments):")
            for vc in validated:
                status = "VALID INLINE" if vc.get("is_valid_line") else "OFF-DIFF SEGREGATED"
                logger.info(f" [{status}] {vc['path']}:{vc['line']} ({vc['severity']}) [is_valid_line={vc.get('is_valid_line')}]")

            self.assertTrue(validated[0]["is_valid_line"])
            self.assertTrue(validated[1]["is_valid_line"])
            self.assertFalse(validated[2]["is_valid_line"])

            logger.info(f"[PR #{pr_number}] Step 4: Simulating batch review creation & verifying safety...")
            mock_github = MagicMock()
            mock_repo = MagicMock()
            mock_pr = MagicMock()
            mock_github.get_repo.return_value = mock_repo
            mock_repo.get_pull.return_value = mock_pr
            mock_pr.get_commits.return_value = [MagicMock()]

            commenter = GitHubCommenter(mock_github, "Mineraculous/Mineraculous", pr_number)
            commenter.post_review_comments(validated)

            mock_pr.create_review_comment.assert_not_called()
            mock_pr.create_issue_comment.assert_not_called()
            mock_pr.create_review.assert_called_once()
            review_kwargs = mock_pr.create_review.call_args[1]

            for bic in review_kwargs["comments"]:
                self.assertTrue(parsed_diff.is_line_in_diff(bic["path"], bic["line"]))

            self.assertIn("Off-diff architectural note", review_kwargs["body"])
            logger.info(f"[PR #{pr_number}] Batch review verified: {len(review_kwargs['comments'])} valid inline comments, off-diff notes in body.")
            logger.info(f"[PR #{pr_number}] 0 individual comments posted (secondary rate limit avoided).")

        logger.info("=" * 70)
        logger.info("ALL LIVE OPEN PRS VERIFIED SUCCESSFULLY.")
        logger.info("=" * 70)

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
        from src.models_resolver import resolve_model_name
        model_to_use = resolve_model_name(reviewer.client, "auto")
        response_text = reviewer._execute_review_generation(model_to_use, prompt, enable_tools=False)
        self.assertIsNotNone(response_text)
        comments = reviewer._parse_and_repair_json(response_text)
        self.assertIsInstance(comments, list)


if __name__ == "__main__":
    unittest.main()
