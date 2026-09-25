import unittest
from unittest.mock import patch, MagicMock

from src.cli import build_parser, main


class TestCli(unittest.TestCase):
    def test_parser_defaults(self):
        parser = build_parser()
        args = parser.parse_args([])
        self.assertFalse(args.staged)
        self.assertFalse(args.unstaged)
        self.assertFalse(args.branch)
        self.assertEqual(args.model, "auto")
        self.assertEqual(args.fail_on, "critical")

    def test_parser_staged_flag(self):
        parser = build_parser()
        args = parser.parse_args(["review", "--staged"])
        self.assertTrue(args.staged)

    def test_parser_branch_with_base(self):
        parser = build_parser()
        args = parser.parse_args(["review", "--branch", "--base", "origin/master"])
        self.assertTrue(args.branch)
        self.assertEqual(args.base, "origin/master")

    def test_parser_output_file(self):
        parser = build_parser()
        args = parser.parse_args(["-o", "report.md"])
        self.assertEqual(args.output, "report.md")

    def test_parser_paths_with_and_without_review(self):
        parser = build_parser()
        args1 = parser.parse_args(["review", "src/Foo.java"])
        self.assertEqual(args1.paths, ["src/Foo.java"])

        args2 = parser.parse_args(["src/Foo.java"])
        self.assertEqual(args2.paths, ["src/Foo.java"])

        args3 = parser.parse_args(["review"])
        self.assertEqual(args3.paths, [])


    @patch("src.cli.get_git_root")
    def test_main_not_in_git_repo_fails(self, mock_get_root):
        mock_get_root.return_value = None
        exit_code = main(["review"])
        self.assertEqual(exit_code, 1)

    @patch("src.cli.get_git_root")
    @patch("src.cli.extract_git_diff")
    def test_main_no_changes(self, mock_diff, mock_root):
        mock_root.return_value = "/path/to/repo"
        mock_diff.return_value = ("", "Uncommitted changes")
        exit_code = main(["review"])
        self.assertEqual(exit_code, 0)

    @patch("src.cli.get_git_root")
    @patch("src.cli.extract_git_diff")
    @patch("src.cli.TommiConfig.for_local")
    @patch("src.cli.TommiReviewer")
    def test_main_review_success_clean(self, mock_reviewer_cls, mock_config, mock_diff, mock_root):
        mock_root.return_value = "/path/to/repo"
        mock_diff.return_value = (
            "diff --git a/Test.java b/Test.java\n--- a/Test.java\n+++ b/Test.java\n@@ -1 +1 @@\n+int x = 1;\n",
            "Working changes",
        )
        mock_reviewer = MagicMock()
        mock_reviewer.review_diff.return_value = []
        mock_reviewer_cls.return_value = mock_reviewer

        exit_code = main(["review", "--no-color"])
        self.assertEqual(exit_code, 0)
        mock_reviewer.review_diff.assert_called_once()

    @patch("src.cli.get_git_root")
    @patch("src.cli.extract_git_diff")
    @patch("src.cli.TommiConfig.for_local")
    @patch("src.cli.TommiReviewer")
    def test_main_review_critical_failure_exit_code(self, mock_reviewer_cls, mock_config, mock_diff, mock_root):
        mock_root.return_value = "/path/to/repo"
        mock_diff.return_value = (
            "diff --git a/Test.java b/Test.java\n--- a/Test.java\n+++ b/Test.java\n@@ -1 +1 @@\n+int x = 1;\n",
            "Working changes",
        )
        mock_reviewer = MagicMock()
        mock_reviewer.review_diff.return_value = [
            {"path": "Test.java", "line": 1, "severity": "CRITICAL", "body": "Bad bug"}
        ]
        mock_reviewer_cls.return_value = mock_reviewer

        # Default fail_on is 'critical'
        exit_code = main(["review", "--no-color"])
        self.assertEqual(exit_code, 1)

    @patch("src.cli.get_git_root")
    @patch("src.cli.extract_git_diff")
    @patch("src.cli.TommiConfig.for_local")
    @patch("src.cli.TommiReviewer")
    def test_main_review_fail_on_never(self, mock_reviewer_cls, mock_config, mock_diff, mock_root):
        mock_root.return_value = "/path/to/repo"
        mock_diff.return_value = (
            "diff --git a/Test.java b/Test.java\n--- a/Test.java\n+++ b/Test.java\n@@ -1 +1 @@\n+int x = 1;\n",
            "Working changes",
        )
        mock_reviewer = MagicMock()
        mock_reviewer.review_diff.return_value = [
            {"path": "Test.java", "line": 1, "severity": "CRITICAL", "body": "Bad bug"}
        ]
        mock_reviewer_cls.return_value = mock_reviewer

        exit_code = main(["review", "--fail-on", "never", "--no-color"])
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
