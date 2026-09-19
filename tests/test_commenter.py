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
        # 65 comments: 30 in first review, 30 in second review, 5 in third review
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

        self.assertEqual(self.mock_pr.create_review.call_count, 3)
        call_1_kwargs = self.mock_pr.create_review.call_args_list[0][1]
        call_2_kwargs = self.mock_pr.create_review.call_args_list[1][1]
        call_3_kwargs = self.mock_pr.create_review.call_args_list[2][1]

        self.assertEqual(len(call_1_kwargs["comments"]), 30)
        self.assertIn("65 Warnings", call_1_kwargs["body"])

        self.assertEqual(len(call_2_kwargs["comments"]), 30)
        self.assertIn("Part 2", call_2_kwargs["body"])

        self.assertEqual(len(call_3_kwargs["comments"]), 5)
        self.assertIn("Part 3", call_3_kwargs["body"])

        # Pacing: 5s sleep between chunks
        mock_sleep.assert_called_with(5)

    def test_post_review_comments_capping_and_excess_details(self):
        # When max_inline_comments = 30 is explicitly configured
        self.commenter.max_inline_comments = 30
        comments = []
        # 5 critical
        for i in range(5):
            comments.append({
                "path": f"src/Critical{i}.java",
                "line": i + 1,
                "body": f"Critical issue {i}",
                "severity": "CRITICAL",
                "is_valid_line": True,
            })
        # 10 warnings
        for i in range(10):
            comments.append({
                "path": f"src/Warning{i}.java",
                "line": i + 1,
                "body": f"Warning issue {i}",
                "severity": "WARNING",
                "is_valid_line": True,
            })
        # 25 suggestions
        for i in range(25):
            comments.append({
                "path": f"src/Suggestion{i}.java",
                "line": i + 1,
                "body": f"Suggestion issue {i}",
                "severity": "SUGGESTION",
                "is_valid_line": True,
            })

        self.commenter.post_review_comments(comments)

        self.mock_pr.create_review.assert_called_once()
        kwargs = self.mock_pr.create_review.call_args[1]
        batch_comments = kwargs["comments"]
        review_body = kwargs["body"]

        # Exactly 30 inline comments
        self.assertEqual(len(batch_comments), 30)

        # All 5 critical are placed first
        for i in range(5):
            self.assertEqual(batch_comments[i]["path"], f"src/Critical{i}.java")
            self.assertIn("[CRITICAL]", batch_comments[i]["body"])

        # All 10 warnings are placed next
        for i in range(5, 15):
            self.assertEqual(batch_comments[i]["path"], f"src/Warning{i - 5}.java")
            self.assertIn("[WARNING]", batch_comments[i]["body"])

        # 15 suggestions are inline
        for i in range(15, 30):
            self.assertEqual(batch_comments[i]["path"], f"src/Suggestion{i - 15}.java")
            self.assertIn("[SUGGESTION]", batch_comments[i]["body"])

        # The remaining 10 suggestions are consolidated in the <details> section
        self.assertIn("<details>", review_body)
        self.assertIn("Additional Findings (10 more summarized)", review_body)
        for i in range(15, 25):
            self.assertIn(f"`src/Suggestion{i}.java:{i + 1}`", review_body)

    def test_post_review_comments_deduplication(self):
        # Mock existing comment on PR
        mock_existing_rc = MagicMock()
        mock_existing_rc.path = "src/Existing.java"
        mock_existing_rc.line = 42
        mock_existing_rc.body = "**[CRITICAL]** Null pointer risk on line 42"
        self.mock_pr.get_review_comments.return_value = [mock_existing_rc]

        comments = [
            # Duplicate of existing PR comment (even without prefix)
            {
                "path": "src/Existing.java",
                "line": 42,
                "body": "Null pointer risk on line 42",
                "severity": "CRITICAL",
                "is_valid_line": True,
            },
            # Duplicate within current review run
            {
                "path": "src/New.java",
                "line": 10,
                "body": "Duplicate new comment",
                "severity": "WARNING",
                "is_valid_line": True,
            },
            {
                "path": "src/New.java",
                "line": 10,
                "body": "**[WARNING]** Duplicate new comment",
                "severity": "WARNING",
                "is_valid_line": True,
            },
            # Unique new comment
            {
                "path": "src/Unique.java",
                "line": 20,
                "body": "Unique comment",
                "severity": "SUGGESTION",
                "is_valid_line": True,
            },
        ]

        self.commenter.post_review_comments(comments)

        self.mock_pr.create_review.assert_called_once()
        kwargs = self.mock_pr.create_review.call_args[1]
        batch_comments = kwargs["comments"]

        # Only 2 comments should survive deduplication: 1 from New.java, 1 from Unique.java
        self.assertEqual(len(batch_comments), 2)
        paths = [c["path"] for c in batch_comments]
        self.assertNotIn("src/Existing.java", paths)
        self.assertIn("src/New.java", paths)
        self.assertIn("src/Unique.java", paths)

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

    def test_post_review_comments_with_summary_note(self):
        comments = [
            {
                "path": "src/Test.java",
                "line": 10,
                "body": "Issue 1",
                "severity": "WARNING",
                "is_valid_line": True,
            }
        ]
        summary_note = "> ⚠️ **Partial Review Notice**: Due to temporary AI API rate limits, 2 file(s) could not be reviewed."

        self.commenter.post_review_comments(comments, summary_note=summary_note)

        self.mock_pr.create_review.assert_called_once()
        kwargs = self.mock_pr.create_review.call_args[1]
        self.assertIn("apply them directly.\n\n" + summary_note, kwargs["body"])


    @patch("src.commenter.time.sleep")
    def test_post_review_comments_secondary_rate_limit_retry_on_chunk(self, mock_sleep):
        # When review chunk encounters 403 secondary rate limit, it backs off 15s and retries
        self.mock_pr.create_review.side_effect = [
            GithubException(403, {"message": "You have exceeded a secondary rate limit. Please wait a few minutes."}),
            MagicMock(),
        ]
        comments = [
            {
                "path": "src/Test.java",
                "line": 10,
                "body": "Issue 1",
                "severity": "WARNING",
                "is_valid_line": True,
            }
        ]

        self.commenter.post_review_comments(comments)

        self.assertEqual(self.mock_pr.create_review.call_count, 2)
        mock_sleep.assert_called_with(15)


if __name__ == "__main__":
    unittest.main()
