import unittest
from unittest.mock import MagicMock, patch
from github import GithubException
from src.commenter import GitHubCommenter


class TestGitHubCommenter(unittest.TestCase):
    def setUp(self):
        self.mock_github = MagicMock()
        self.mock_repo = MagicMock()
        self.mock_pr = MagicMock()
        self.mock_github.get_repo.return_value = self.mock_repo
        self.mock_repo.get_pull.return_value = self.mock_pr

        self.mock_commit = MagicMock()
        self.mock_pr.get_commits.return_value = [self.mock_commit]

        self.commenter = GitHubCommenter(
            github_client=self.mock_github,
            repo_name="owner/repo",
            pr_number=87,
        )

    def test_post_review_comments_empty(self):
        self.commenter.post_review_comments([])
        self.mock_pr.create_review.assert_not_called()
        self.mock_pr.create_issue_comment.assert_not_called()

    def test_post_review_comments_all_valid(self):
        comments = [
            {
                "path": "src/Test1.java",
                "line": 10,
                "body": "Issue in Test1",
                "severity": "CRITICAL",
                "is_valid_line": True,
            },
            {
                "path": "src/Test2.java",
                "line": 20,
                "body": "Issue in Test2",
                "severity": "WARNING",
                "is_valid_line": True,
            },
        ]

        self.commenter.post_review_comments(comments)

        self.mock_pr.create_review.assert_called_once()
        kwargs = self.mock_pr.create_review.call_args[1]
        self.assertEqual(kwargs["commit"], self.mock_commit)
        self.assertEqual(kwargs["event"], "COMMENT")
        self.assertIn("1 Critical", kwargs["body"])
        self.assertIn("1 Warning", kwargs["body"])
        self.assertNotIn("Additional Review Notes", kwargs["body"])

        batch_comments = kwargs["comments"]
        self.assertEqual(len(batch_comments), 2)
        self.assertEqual(batch_comments[0]["path"], "src/Test1.java")
        self.assertEqual(batch_comments[0]["line"], 10)
        self.assertEqual(batch_comments[0]["side"], "RIGHT")
        self.assertEqual(batch_comments[0]["body"], "**[CRITICAL]** Issue in Test1")

        self.mock_pr.create_issue_comment.assert_not_called()

    def test_post_review_comments_mixed_valid_and_off_diff(self):
        comments = [
            {
                "path": "src/Valid.java",
                "line": 15,
                "body": "Valid line issue",
                "severity": "WARNING",
                "is_valid_line": True,
            },
            {
                "path": "src/OffDiff.java",
                "line": 293,
                "body": "Outside diff range issue",
                "severity": "SUGGESTION",
                "is_valid_line": False,
            },
        ]

        self.commenter.post_review_comments(comments)

        self.mock_pr.create_review.assert_called_once()
        kwargs = self.mock_pr.create_review.call_args[1]

        # Only the valid line comment is in batch_comments
        batch_comments = kwargs["comments"]
        self.assertEqual(len(batch_comments), 1)
        self.assertEqual(batch_comments[0]["path"], "src/Valid.java")

        # The off-diff comment is in the review body as an unplaced note
        review_body = kwargs["body"]
        self.assertIn("**Additional Review Notes** (outside diff range):", review_body)
        self.assertIn("`src/OffDiff.java:293`", review_body)
        self.assertIn("[SUGGESTION]: Outside diff range issue", review_body)

        self.mock_pr.create_issue_comment.assert_not_called()

    def test_post_review_comments_all_off_diff(self):
        comments = [
            {
                "path": "src/OffDiff.java",
                "line": 500,
                "body": "Off diff note",
                "severity": "WARNING",
                "is_valid_line": False,
            }
        ]

        self.commenter.post_review_comments(comments)

        # Batch review is bypassed, directly creates an issue comment
        self.mock_pr.create_review.assert_not_called()
        self.mock_pr.create_issue_comment.assert_called_once()
        body = self.mock_pr.create_issue_comment.call_args[0][0]
        self.assertIn("**Additional Review Notes** (outside diff range):", body)
        self.assertIn("`src/OffDiff.java:500`", body)

    @patch("src.commenter.time.sleep")
    def test_post_review_comments_chunking_large_batches(self, mock_sleep):
        # 65 comments: 50 in first review, 15 in second review
        comments = [
            {
                "path": f"src/File{i}.java",
                "line": i + 1,
                "body": f"Comment {i}",
                "severity": "WARNING",
                "is_valid_line": True,
            }
            for i in range(65)
        ]

        self.commenter.post_review_comments(comments)

        self.assertEqual(self.mock_pr.create_review.call_count, 2)
        call_1_kwargs = self.mock_pr.create_review.call_args_list[0][1]
        call_2_kwargs = self.mock_pr.create_review.call_args_list[1][1]

        self.assertEqual(len(call_1_kwargs["comments"]), 50)
        self.assertIn("65 Warnings", call_1_kwargs["body"])

        self.assertEqual(len(call_2_kwargs["comments"]), 15)
        self.assertIn("Part 2", call_2_kwargs["body"])
        mock_sleep.assert_called_once_with(1)

    @patch("src.commenter.time.sleep")
    def test_post_review_comments_fallback_on_batch_failure(self, mock_sleep):
        self.mock_pr.create_review.side_effect = GithubException(
            422, {"message": "Validation Failed", "errors": ["simulated error"]}
        )

        comments = [
            {
                "path": "src/Test1.java",
                "line": 10,
                "body": "Issue 1",
                "severity": "WARNING",
                "is_valid_line": True,
            },
            {
                "path": "src/OffDiff.java",
                "line": 999,
                "body": "Issue 2",
                "severity": "SUGGESTION",
                "is_valid_line": False,
            },
        ]

        self.commenter.post_review_comments(comments)

        # Only the valid line comment is attempted individually
        self.mock_pr.create_review_comment.assert_called_once_with(
            body="**[WARNING]** Issue 1",
            commit=self.mock_commit,
            path="src/Test1.java",
            line=10,
            side="RIGHT",
        )
        mock_sleep.assert_called_once_with(1)

        # Fallback issue comment contains the unplaced off-diff comment
        self.mock_pr.create_issue_comment.assert_called_once()
        issue_body = self.mock_pr.create_issue_comment.call_args[0][0]
        self.assertIn("**Additional Review Notes** (unable to place inline):", issue_body)
        self.assertIn("`src/OffDiff.java:999`", issue_body)

    @patch("src.commenter.time.sleep")
    def test_post_review_comments_fallback_secondary_rate_limit(self, mock_sleep):
        self.mock_pr.create_review.side_effect = GithubException(
            422, {"message": "Validation Failed"}
        )

        self.mock_pr.create_review_comment.side_effect = GithubException(
            403, {"message": "You have exceeded a secondary rate limit. Please wait a few minutes."}
        )

        comments = [
            {
                "path": "src/Test.java",
                "line": 10,
                "body": "Issue 1",
                "severity": "CRITICAL",
                "is_valid_line": True,
            }
        ]

        self.commenter.post_review_comments(comments)

        # Should have backed off for 10s on secondary rate limit
        mock_sleep.assert_called_with(10)

        # The failed comment should be in the fallback issue comment
        self.mock_pr.create_issue_comment.assert_called_once()
        issue_body = self.mock_pr.create_issue_comment.call_args[0][0]
        self.assertIn("`src/Test.java:10`", issue_body)


if __name__ == "__main__":
    unittest.main()
