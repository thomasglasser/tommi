import logging
from typing import List, Optional
from github import Github, GithubException
from github.PullRequest import PullRequest
from github.Commit import Commit

logger = logging.getLogger("tommi.commenter")


class GitHubCommenter:
    def __init__(self, github_client: Github, repo_name: str, pr_number: int, comment_id: Optional[int] = None):
        self.g = github_client
        self.repo_name = repo_name
        self.pr_number = pr_number
        self.comment_id = comment_id
        self.repo = self.g.get_repo(self.repo_name)
        self.pr: PullRequest = self.repo.get_pull(self.pr_number)

    def add_reaction(self, reaction: str) -> None:
        """Adds a reaction to the triggering comment if comment_id is present."""
        if not self.comment_id:
            return
        try:
            # Check review comments first
            comment = self.pr.get_comment(self.comment_id)
            comment.create_reaction(reaction)
            logger.info(f"Added reaction '{reaction}' to review comment #{self.comment_id}")
            return
        except Exception:
            pass

        try:
            # Fallback to issue comment
            comment = self.pr.as_issue().get_comment(self.comment_id)
            comment.create_reaction(reaction)
            logger.info(f"Added reaction '{reaction}' to issue comment #{self.comment_id}")
        except Exception as e:
            logger.warning(f"Failed to add reaction '{reaction}': {e}")

    def get_latest_commit(self) -> Commit:
        """Retrieves the latest commit in the pull request."""
        commits = self.pr.get_commits()
        return commits.reversed[0]

    def post_review_comments(self, comments: List[dict]) -> None:
        """
        Posts review comments to the PR using GitHub's Batch Review API in a single HTTP request.
        Falls back to individual comment posting if batch submission encounters validation errors.
        """
        if not comments:
            logger.info("No comments to post.")
            return

        latest_commit = self.get_latest_commit()

        # Count severities
        critical_count = sum(1 for c in comments if c.get("severity") == "CRITICAL")
        warning_count = sum(1 for c in comments if c.get("severity") == "WARNING")
        suggestion_count = sum(1 for c in comments if c.get("severity") == "SUGGESTION")

        scorecard_parts = []
        if critical_count:
            scorecard_parts.append(f"🚨 **{critical_count} Critical**")
        if warning_count:
            scorecard_parts.append(f"⚠️ **{warning_count} Warning{'s' if warning_count != 1 else ''}**")
        if suggestion_count:
            scorecard_parts.append(f"💡 **{suggestion_count} Suggestion{'s' if suggestion_count != 1 else ''}**")

        scorecard_str = " | ".join(scorecard_parts) if scorecard_parts else "✨ Clean"
        summary_header = (
            f"### 🤖 T.O.M.M.I. Code Review\n\n"
            f"**Review Findings**: {scorecard_str}\n\n"
            f"Please review the inline feedback below. For suggestions with code blocks, you can apply them directly."
        )

        # Build batch comments payload
        batch_comments = []
        for item in comments:
            path = item.get("path")
            line = item.get("line")
            body = item.get("body")
            severity = item.get("severity", "WARNING")

            if not path or not line or not body:
                continue

            severity_prefix = f"**[{severity}]** "
            formatted_body = body if body.startswith(severity_prefix) or body.startswith(f"[{severity}]") else f"{severity_prefix}{body}"

            batch_comments.append({
                "path": path,
                "line": int(line),
                "body": formatted_body,
                "side": "RIGHT"
            })

        # 1. Try Batch Review Submission (1 API Call)
        try:
            self.pr.create_review(
                commit=latest_commit,
                body=summary_header,
                comments=batch_comments,
                event="COMMENT"
            )
            logger.info(f"Successfully posted batch review with {len(batch_comments)} inline comment(s).")
            return
        except GithubException as batch_err:
            logger.warning(f"Batch review creation failed ({batch_err.data.get('message', str(batch_err))}), falling back to individual comments...")

        # 2. Fallback: Post comments individually if batch review fails
        placed_count = 0
        unplaced_comments = []

        for item in comments:
            path = item.get("path")
            line = item.get("line")
            body = item.get("body")
            severity = item.get("severity", "WARNING")

            if not path or not line or not body:
                continue

            severity_prefix = f"**[{severity}]** "
            formatted_body = body if body.startswith(severity_prefix) or body.startswith(f"[{severity}]") else f"{severity_prefix}{body}"

            try:
                self.pr.create_review_comment(
                    body=formatted_body,
                    commit=latest_commit,
                    path=path,
                    line=int(line),
                    side="RIGHT"
                )
                placed_count += 1
                logger.info(f"Posted inline comment [{severity}] on {path}:{line}")
            except GithubException as e:
                logger.warning(f"Could not post inline comment on {path}:{line}: {e.data.get('message', str(e))}")
                unplaced_comments.append(f"- **`{path}:{line}`** [{severity}]: {body}")

        # Post top-level summary / unplaced comments
        fallback_body = summary_header
        if unplaced_comments:
            fallback_body += (
                "\n\n**Additional Review Notes** (unable to place inline):\n"
                + "\n".join(unplaced_comments)
            )
        self.pr.create_issue_comment(fallback_body)
        logger.info(f"Fallback review completed: {placed_count} inline comments posted, {len(unplaced_comments)} unplaced.")

    def post_issue_comment(self, body: str) -> None:
        """Posts a general comment on the PR / Issue thread."""
        try:
            self.pr.create_issue_comment(body)
        except Exception as e:
            logger.error(f"Failed to post issue comment: {e}")

    def reply_to_comment(self, body: str) -> None:
        """Replies directly in the review comment thread if applicable, otherwise posts a general comment."""
        if self.comment_id:
            try:
                self.pr.create_review_comment_reply(self.comment_id, body)
                logger.info(f"Replied in review comment thread #{self.comment_id}")
                return
            except Exception:
                pass
        self.post_issue_comment(body)
