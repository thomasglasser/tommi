import logging
import time
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
        """Adds a reaction to the triggering comment if comment_id is present, or to the PR issue description."""
        if self.comment_id:
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
                return
            except Exception as e:
                logger.warning(f"Failed to add reaction '{reaction}': {e}")
        else:
            try:
                self.pr.as_issue().create_reaction(reaction)
                logger.info(f"Added reaction '{reaction}' to PR #{self.pr_number}")
            except Exception as e:
                logger.warning(f"Failed to add reaction '{reaction}' to PR #{self.pr_number}: {e}")

    def get_latest_commit(self) -> Commit:
        """Retrieves the latest commit in the pull request."""
        commits = self.pr.get_commits()
        if hasattr(commits, "reversed"):
            return commits.reversed[0]
        return commits[-1]

    def post_review_comments(self, comments: List[dict], summary_note: Optional[str] = None) -> None:
        """
        Posts review comments to the PR using GitHub's Batch Review API in a single HTTP request.
        Segregates off-diff comments into unplaced summary notes to guarantee batch submission succeeds.
        Falls back to paced individual comment posting if batch submission encounters validation errors.
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
        if summary_note:
            summary_header = f"{summary_header}\n\n{summary_note}"

        # Build batch comments and unplaced notes payloads
        batch_comments = []
        unplaced_notes = []

        for item in comments:
            path = item.get("path")
            line = item.get("line")
            body = item.get("body")
            severity = item.get("severity", "WARNING")
            is_valid_line = item.get("is_valid_line", True)

            if not path or line is None or not body:
                continue

            try:
                line_int = int(line)
                if line_int <= 0:
                    continue
            except (ValueError, TypeError):
                continue

            severity_prefix = f"**[{severity}]** "
            formatted_body = body if body.startswith(severity_prefix) or body.startswith(f"[{severity}]") else f"{severity_prefix}{body}"

            if is_valid_line:
                batch_comments.append({
                    "path": path,
                    "line": line_int,
                    "body": formatted_body,
                    "side": "RIGHT"
                })
            else:
                unplaced_notes.append(f"- **`{path}:{line}`** [{severity}]: {body}")

        full_review_body = summary_header
        if unplaced_notes:
            full_review_body += (
                "\n\n**Additional Review Notes** (outside diff range):\n"
                + "\n".join(unplaced_notes)
            )

        if not batch_comments:
            self.pr.create_issue_comment(full_review_body)
            logger.info(f"All review comments were outside the diff range. Posted {len(unplaced_notes)} note(s) as an issue comment.")
            return

        # 1. Try Batch Review Submission (chunks of up to 50 comments to respect GitHub limits)
        max_comments_per_review = 50
        chunks = [
            batch_comments[i:i + max_comments_per_review]
            for i in range(0, len(batch_comments), max_comments_per_review)
        ]

        try:
            for idx, chunk in enumerate(chunks):
                chunk_body = full_review_body if idx == 0 else f"### 🤖 T.O.M.M.I. Code Review (Part {idx + 1})\n\nPlease review the inline feedback below."
                self.pr.create_review(
                    commit=latest_commit,
                    body=chunk_body,
                    comments=chunk,
                    event="COMMENT"
                )
                if idx < len(chunks) - 1:
                    time.sleep(1)
            logger.info(
                f"Successfully posted batch review with {len(batch_comments)} inline comment(s)"
                + (f" across {len(chunks)} review(s)" if len(chunks) > 1 else "")
                + (f" and {len(unplaced_notes)} unplaced note(s)." if unplaced_notes else ".")
            )
            return
        except GithubException as batch_err:
            err_msg = batch_err.data.get("message", str(batch_err)) if isinstance(batch_err.data, dict) else str(batch_err)
            errors_detail = batch_err.data.get("errors", []) if isinstance(batch_err.data, dict) else []
            logger.warning(f"Batch review creation failed ({err_msg}, errors: {errors_detail}), falling back to individual comments...")

        # 2. Fallback: Post comments individually if batch review fails
        placed_count = 0
        fallback_unplaced = list(unplaced_notes)

        for item in comments:
            path = item.get("path")
            line = item.get("line")
            body = item.get("body")
            severity = item.get("severity", "WARNING")
            is_valid_line = item.get("is_valid_line", True)

            if not path or line is None or not body:
                continue

            try:
                line_int = int(line)
                if line_int <= 0:
                    continue
            except (ValueError, TypeError):
                continue

            if not is_valid_line:
                continue

            severity_prefix = f"**[{severity}]** "
            formatted_body = body if body.startswith(severity_prefix) or body.startswith(f"[{severity}]") else f"{severity_prefix}{body}"

            try:
                self.pr.create_review_comment(
                    body=formatted_body,
                    commit=latest_commit,
                    path=path,
                    line=line_int,
                    side="RIGHT"
                )
                placed_count += 1
                logger.info(f"Posted inline comment [{severity}] on {path}:{line}")
                time.sleep(1)  # Pace requests to prevent triggering GitHub secondary rate limits
            except GithubException as e:
                err_msg = e.data.get("message", str(e)) if isinstance(e.data, dict) else str(e)
                errors_detail = e.data.get("errors", []) if isinstance(e.data, dict) else []
                logger.warning(f"Could not post inline comment on {path}:{line}: {err_msg} (errors: {errors_detail})")
                fallback_unplaced.append(f"- **`{path}:{line}`** [{severity}]: {body}")
                if e.status in (403, 429) or (e.status == 422 and "secondary rate limit" in str(e.data).lower()):
                    logger.warning("GitHub secondary rate limit encountered during fallback. Backing off for 10s...")
                    time.sleep(10)

        # Post top-level summary / unplaced comments
        fallback_body = summary_header
        if fallback_unplaced:
            fallback_body += (
                "\n\n**Additional Review Notes** (unable to place inline):\n"
                + "\n".join(fallback_unplaced)
            )
        self.pr.create_issue_comment(fallback_body)
        logger.info(f"Fallback review completed: {placed_count} inline comments posted, {len(fallback_unplaced)} unplaced.")

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
