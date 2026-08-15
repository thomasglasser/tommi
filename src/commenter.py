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
        Posts review comments to the PR. Attempts inline placement first,
        collecting any failed comments into a fallback comment.
        """
        if not comments:
            logger.info("No comments to post.")
            return

        latest_commit = self.get_latest_commit()
        unplaced_comments = []
        placed_count = 0

        for item in comments:
            path = item.get("path")
            line = item.get("line")
            body = item.get("body")

            if not path or not line or not body:
                continue

            try:
                self.pr.create_review_comment(
                    body=body,
                    commit=latest_commit,
                    path=path,
                    line=line,
                    side="RIGHT"
                )
                placed_count += 1
                logger.info(f"Posted inline comment on {path}:{line}")
            except GithubException as e:
                logger.warning(f"Could not post inline comment on {path}:{line}: {e.data.get('message', str(e))}")
                unplaced_comments.append(f"- **`{path}:{line}`**: {body}")

        if unplaced_comments:
            fallback_body = (
                "### 🔍 T.O.M.M.I. Additional Review Notes\n"
                "I couldn't attach the following comments directly to specific diff lines, but please review them:\n\n"
                + "\n".join(unplaced_comments)
            )
            self.pr.create_issue_comment(fallback_body)
            logger.info(f"Posted fallback comment with {len(unplaced_comments)} notes.")

        logger.info(f"Review completed: {placed_count} inline comments posted, {len(unplaced_comments)} fallbacks.")

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
