import logging
import time
from typing import List, Optional
from github import Github, GithubException
from github.PullRequest import PullRequest
from github.Commit import Commit

logger = logging.getLogger("tommi.commenter")


class GitHubCommenter:
    def __init__(
        self,
        github_client: Github,
        repo_name: str,
        pr_number: int,
        comment_id: Optional[int] = None,
        max_inline_comments: Optional[int] = None,
    ):
        self.g = github_client
        self.repo_name = repo_name
        self.pr_number = pr_number
        self.comment_id = comment_id
        self.max_inline_comments = max_inline_comments
        self.repo = self.g.get_repo(self.repo_name)
        self.pr: PullRequest = self.repo.get_pull(self.pr_number)

    @staticmethod
    def _normalize_comment_key(path: str, line: int, body: str) -> tuple:
        first_line = (body or "").strip().split("\n", 1)[0].strip()
        clean = first_line
        for pfx in ("**[CRITICAL]**", "**[WARNING]**", "**[SUGGESTION]**", "[CRITICAL]", "[WARNING]", "[SUGGESTION]"):
            if clean.startswith(pfx):
                clean = clean[len(pfx):].strip()
                break
        return (path, line, clean[:60].lower())

    def _fetch_existing_comment_keys(self) -> set:
        """
        Fetches existing inline review comments on the PR to prevent duplicate comments.
        Returns a set of (path, line, clean_first_line).
        """
        existing = set()
        try:
            for rc in self.pr.get_review_comments():
                path = getattr(rc, "path", None)
                line = getattr(rc, "line", None) or getattr(rc, "original_line", None)
                body = getattr(rc, "body", "") or ""
                if path and line is not None:
                    try:
                        line_int = int(line)
                        existing.add(self._normalize_comment_key(path, line_int, body))
                    except (ValueError, TypeError):
                        continue
        except Exception as e:
            logger.warning(f"Could not fetch existing PR review comments for deduplication: {e}")
        return existing

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
        Posts review comments to the PR using GitHub's Batch Review API.
        Deduplicates against already posted comments, prioritizes by severity,
        caps inline comments to max_inline_comments (default 30) to prevent review spam,
        and consolidates excess comments into an expandable summary details block.
        """
        if not comments:
            logger.info("No comments to post.")
            return

        latest_commit = self.get_latest_commit()

        # Deduplicate incoming comments against existing PR review comments and intra-run duplicates
        existing_keys = self._fetch_existing_comment_keys()
        seen_in_run = set()
        valid_items = []

        for item in comments:
            path = item.get("path")
            line = item.get("line")
            body = item.get("body")
            severity = item.get("severity", "WARNING")

            if not path or line is None or not body:
                continue

            try:
                line_int = int(line)
                if line_int <= 0:
                    continue
            except (ValueError, TypeError):
                continue

            comment_key = self._normalize_comment_key(path, line_int, body)
            if comment_key in existing_keys:
                logger.info(f"Skipping comment on {path}:{line_int} as it already exists on this PR.")
                continue
            if comment_key in seen_in_run:
                logger.info(f"Skipping duplicate comment within current review run on {path}:{line_int}.")
                continue

            seen_in_run.add(comment_key)
            valid_items.append(dict(item, line=line_int))

        if not valid_items:
            logger.info("All review comments were duplicates of existing comments or invalid. Nothing to post.")
            return

        # Prioritize comments: CRITICAL > WARNING > SUGGESTION
        severity_rank = {"CRITICAL": 0, "WARNING": 1, "SUGGESTION": 2}
        valid_items.sort(key=lambda x: severity_rank.get(x.get("severity", "WARNING"), 3))

        # Count severities across valid new items
        critical_count = sum(1 for c in valid_items if c.get("severity") == "CRITICAL")
        warning_count = sum(1 for c in valid_items if c.get("severity") == "WARNING")
        suggestion_count = sum(1 for c in valid_items if c.get("severity") == "SUGGESTION")

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

        # Segregate inline candidates from off-diff unplaced notes
        inline_candidates = [item for item in valid_items if item.get("is_valid_line", True)]
        unplaced_items = [item for item in valid_items if not item.get("is_valid_line", True)]

        # Cap inline comments if max_inline_comments is configured, otherwise post all in batches
        if self.max_inline_comments and self.max_inline_comments > 0:
            top_inline = inline_candidates[:self.max_inline_comments]
            excess_inline = inline_candidates[self.max_inline_comments:]
        else:
            top_inline = inline_candidates
            excess_inline = []

        batch_comments = []
        for item in top_inline:
            severity = item.get("severity", "WARNING")
            body = item.get("body", "")
            severity_prefix = f"**[{severity}]** "
            formatted_body = body if body.startswith(severity_prefix) or body.startswith(f"[{severity}]") else f"{severity_prefix}{body}"
            batch_comments.append({
                "path": item["path"],
                "line": item["line"],
                "body": formatted_body,
                "side": "RIGHT",
            })

        unplaced_notes = []
        for item in unplaced_items:
            path = item.get("path")
            line = item.get("line")
            severity = item.get("severity", "WARNING")
            body = item.get("body", "").strip()
            unplaced_notes.append(f"- **`{path}:{line}`** [{severity}]: {body}")

        excess_notes = []
        for item in excess_inline:
            path = item.get("path")
            line = item.get("line")
            severity = item.get("severity", "WARNING")
            body = item.get("body", "").strip()
            excess_notes.append(f"- **`{path}:{line}`** [{severity}]: {body}")

        full_review_body = summary_header
        if unplaced_notes:
            full_review_body += (
                "\n\n**Additional Review Notes** (outside diff range):\n"
                + "\n".join(unplaced_notes)
            )
        if excess_notes:
            full_review_body += (
                f"\n\n<details>\n<summary>📋 <b>Additional Findings ({len(excess_notes)} more summarized)</b></summary>\n\n"
                + "\n".join(excess_notes)
                + "\n\n</details>"
            )

        if not batch_comments:
            self.pr.create_issue_comment(full_review_body)
            logger.info(f"No inline comments posted to diff. Posted {len(unplaced_notes) + len(excess_notes)} note(s) as an issue comment.")
            return

        # 1. Try Batch Review Submission (chunks of up to 30 comments)
        max_comments_per_review = 30
        chunks = [
            batch_comments[i:i + max_comments_per_review]
            for i in range(0, len(batch_comments), max_comments_per_review)
        ]

        try:
            for idx, chunk in enumerate(chunks):
                chunk_body = full_review_body if idx == 0 else f"### 🤖 T.O.M.M.I. Code Review (Part {idx + 1})\n\nPlease review the inline feedback below."
                try:
                    self.pr.create_review(
                        commit=latest_commit,
                        body=chunk_body,
                        comments=chunk,
                        event="COMMENT"
                    )
                except GithubException as chunk_err:
                    if chunk_err.status in (403, 429) or (chunk_err.status == 422 and "secondary rate limit" in str(chunk_err.data).lower()):
                        logger.warning(f"Secondary rate limit on review part {idx + 1}. Backing off for 15s...")
                        time.sleep(15)
                        self.pr.create_review(
                            commit=latest_commit,
                            body=chunk_body,
                            comments=chunk,
                            event="COMMENT"
                        )
                    else:
                        raise chunk_err

                if idx < len(chunks) - 1:
                    time.sleep(5)
            logger.info(
                f"Successfully posted batch review with {len(batch_comments)} inline comment(s)"
                + (f" across {len(chunks)} review(s)" if len(chunks) > 1 else "")
                + (f" and {len(unplaced_notes) + len(excess_notes)} summarized note(s)." if (unplaced_notes or excess_notes) else ".")
            )
            return
        except GithubException as batch_err:
            err_msg = batch_err.data.get("message", str(batch_err)) if isinstance(batch_err.data, dict) else str(batch_err)
            errors_detail = batch_err.data.get("errors", []) if isinstance(batch_err.data, dict) else []
            logger.warning(f"Batch review creation failed ({err_msg}, errors: {errors_detail})")

            # Check if GitHub created the review anyway despite returning an error (e.g. secondary rate limit on response)
            try:
                recent_reviews = list(self.pr.get_reviews())
                if recent_reviews and recent_reviews[-1].body and "T.O.M.M.I. Code Review" in recent_reviews[-1].body:
                    logger.info(f"Verified review #{recent_reviews[-1].id} was created on GitHub despite exception.")
                    return
            except Exception as check_err:
                logger.debug(f"Could not verify existing reviews: {check_err}")

            logger.warning("Falling back to individual comments...")

        # 2. Fallback: Post comments individually if batch review fails
        placed_count = 0
        fallback_unplaced = list(unplaced_notes)

        for item in top_inline:
            path = item["path"]
            line = item["line"]
            body = item["body"]
            severity = item.get("severity", "WARNING")

            severity_prefix = f"**[{severity}]** "
            formatted_body = body if body.startswith(severity_prefix) or body.startswith(f"[{severity}]") else f"{severity_prefix}{body}"

            try:
                self.pr.create_review_comment(
                    body=formatted_body,
                    commit=latest_commit,
                    path=path,
                    line=line,
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
        if excess_notes:
            fallback_body += (
                f"\n\n<details>\n<summary>📋 <b>Additional Findings ({len(excess_notes)} more summarized)</b></summary>\n\n"
                + "\n".join(excess_notes)
                + "\n\n</details>"
            )
        self.pr.create_issue_comment(fallback_body)
        logger.info(f"Fallback review completed: {placed_count} inline comments posted, {len(fallback_unplaced) + len(excess_notes)} unplaced/excess.")

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
