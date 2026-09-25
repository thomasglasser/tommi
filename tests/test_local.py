import os
import unittest
from unittest.mock import patch, MagicMock

from src.config import TommiConfig
from src.local import (
    get_git_root,
    get_current_branch,
    get_default_branch,
    has_uncommitted_changes,
    extract_git_diff,
    format_terminal_review,
    format_markdown_report,
)


class TestLocalReview(unittest.TestCase):
    def test_tommi_config_for_local_with_explicit_key(self):
        cfg = TommiConfig.for_local(gemini_api_key="test_key_123", model_name="gemini-3.8-flash")
        self.assertEqual(cfg.gemini_api_key, "test_key_123")
        self.assertEqual(cfg.model_name, "gemini-3.8-flash")
        self.assertEqual(cfg.pr_number, 0)
        self.assertEqual(cfg.event_name, "local_review")

    @patch.dict(os.environ, {"GEMINI_API_KEY": "env_key_456"})
    def test_tommi_config_for_local_from_env(self):
        cfg = TommiConfig.for_local()
        self.assertEqual(cfg.gemini_api_key, "env_key_456")

    @patch.dict(os.environ, {}, clear=True)
    @patch("os.path.isfile", return_value=False)
    def test_tommi_config_for_local_missing_key_raises(self, mock_isfile):
        # With no env and non-existent workspace .env
        with self.assertRaises(ValueError):
            TommiConfig.for_local(workspace_dir="/non/existent/path")

    @patch("src.local.subprocess.run")
    def test_get_git_root(self, mock_run):
        mock_run.return_value = MagicMock(stdout="/path/to/my_repo\n", returncode=0)
        root = get_git_root("/path/to/my_repo/subdir")
        self.assertEqual(root, "/path/to/my_repo")

    @patch("src.local.subprocess.run")
    def test_get_current_branch(self, mock_run):
        mock_run.return_value = MagicMock(stdout="feature-test\n", returncode=0)
        branch = get_current_branch("/path/to/repo")
        self.assertEqual(branch, "feature-test")

    @patch("src.local.subprocess.run")
    def test_extract_git_diff_staged(self, mock_run):
        mock_run.return_value = MagicMock(stdout="diff --git a/Test.java b/Test.java\n", returncode=0)
        diff, desc = extract_git_diff("/path/to/repo", mode="staged")
        self.assertIn("diff --git", diff)
        self.assertIn("Staged", desc)
        mock_run.assert_called_once_with(
            ["git", "-C", "/path/to/repo", "diff", "--cached"],
            capture_output=True,
            text=True,
            check=True,
        )

    @patch("src.local.subprocess.run")
    def test_extract_git_diff_branch(self, mock_run):
        mock_run.return_value = MagicMock(stdout="diff content\n", returncode=0)
        diff, desc = extract_git_diff("/path/to/repo", mode="branch", base="main")
        self.assertEqual(diff, "diff content\n")
        self.assertIn("main", desc)
        mock_run.assert_called_once_with(
            ["git", "-C", "/path/to/repo", "diff", "main...HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )

    def test_format_terminal_review_clean(self):
        out = format_terminal_review(
            comments=[],
            diff_desc="Uncommitted changes",
            repo_name="Mineraculous",
            branch_name="main",
            model_name="gemini-3.8-flash",
            use_color=False,
        )
        self.assertIn("T.O.M.M.I. Local Code Review", out)
        self.assertIn("Mineraculous", out)
        self.assertIn("Looks clean!", out)

    def test_format_terminal_review_with_findings(self):
        comments = [
            {
                "path": "src/Test.java",
                "line": 42,
                "severity": "CRITICAL",
                "body": "Fix this null pointer risk",
            },
            {
                "path": "src/Test.java",
                "line": 50,
                "severity": "WARNING",
                "body": "Avoid magic numbers",
            },
        ]
        out = format_terminal_review(
            comments=comments,
            diff_desc="Branch changes",
            repo_name="Mineraculous",
            branch_name="feature",
            model_name="gemini-3.8-flash",
            use_color=False,
        )
        self.assertIn("1 Critical", out)
        self.assertIn("1 Warning", out)
        self.assertIn("src/Test.java", out)
        self.assertIn("Line 42", out)
        self.assertIn("Fix this null pointer risk", out)
        self.assertIn("Line 50", out)

    def test_format_markdown_report(self):
        comments = [
            {
                "path": "src/MyClass.java",
                "line": 10,
                "severity": "CRITICAL",
                "body": "Major architectural issue",
            }
        ]
        md = format_markdown_report(
            comments=comments,
            diff_desc="git diff HEAD",
            repo_name="Mineraculous",
            branch_name="main",
            model_name="gemini-3.8-flash",
        )
        self.assertIn("### 🤖 T.O.M.M.I. Local Code Review Report", md)
        self.assertIn("1 Critical", md)
        self.assertIn("src/MyClass.java", md)
        self.assertIn("Major architectural issue", md)


if __name__ == "__main__":
    unittest.main()
