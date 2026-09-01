import logging
import sys
from github import Github

from src.config import TommiConfig
from src.github_auth import GitHubAuthManager
from src.commenter import GitHubCommenter
from src.reviewer import TommiReviewer, QuotaExceededException, HighDemandException
from src.learner import TommiLearner

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("tommi")


from typing import Optional, Tuple

def extract_tommi_command(body: str, is_inline_reply: bool = False) -> Optional[Tuple[str, str]]:
    """
    Parses a comment body to look for explicit /tommi slash commands.
    Returns (command_type, argument_text) or None if no command was invoked.
    """
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("/tommi"):
            lower_line = line.lower()
            if lower_line in ("/tommi", "/tommi help") or lower_line.startswith("/tommi help"):
                return ("help", "")
            elif lower_line == "/tommi review" or lower_line.startswith("/tommi review "):
                return ("review", "")
            elif lower_line.startswith("/tommi learn"):
                return ("learn", line[12:].strip())
            elif lower_line.startswith("/tommi false-positive"):
                return ("false-positive", line[21:].strip())
            elif is_inline_reply:
                # Natural feedback reply directly on an inline review comment
                feedback = line.split("/tommi", 1)[1].strip().lstrip(":, -")
                return ("false-positive", feedback)
            else:
                return ("unrecognized", line)
    return None


def main():
    # Setup logging
    logger.info("Starting T.O.M.M.I. Autonomous Engine...")

    # Load configuration
    try:
        config = TommiConfig.from_env()
    except Exception as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # 0. Safety Guard: Never respond to bot comments to prevent recursive feedback loops
    author_type = config.comment_author_type.lower() if isinstance(config.comment_author_type, str) else ""
    author_name = config.comment_author.lower() if isinstance(config.comment_author, str) else ""
    if author_type == "bot" or author_name.endswith("[bot]"):
        logger.info(f"Ignoring comment from bot author '{config.comment_author}' ({config.comment_author_type}). Aborting.")
        return

    auth_manager = GitHubAuthManager(
        token=config.github_token,
        app_id=config.app_id,
        private_key=config.private_key,
    )

    target_token = auth_manager.get_token_for_repo(config.github_repository)
    target_g = auth_manager.get_client_for_repo(config.github_repository)
    commenter = GitHubCommenter(
        github_client=target_g,
        repo_name=config.github_repository,
        pr_number=config.pr_number,
        comment_id=config.comment_id,
    )

    pr = commenter.pr

    if config.comment_id and not (config.event_name == "pull_request" and config.is_merged):
        try:
            target_comment = pr.get_issue_comment(config.comment_id) if config.event_name == "issue_comment" else pr.get_comment(config.comment_id)
            if target_comment and target_comment.user:
                u_type = target_comment.user.type if isinstance(target_comment.user.type, str) else ""
                u_login = target_comment.user.login if isinstance(target_comment.user.login, str) else ""
                if u_type.lower() == "bot" or u_login.lower().endswith("[bot]"):
                    logger.info(f"Comment #{config.comment_id} was authored by bot user '{u_login}'. Aborting.")
                    return
        except Exception as err:
            logger.debug(f"Could not verify comment user type via GitHub API: {err}")

    # Determine command invocation
    is_inline_reply = bool(config.in_reply_to_id)
    cmd_info = extract_tommi_command(config.comment_body, is_inline_reply=is_inline_reply)

    # If triggered by a comment event but no /tommi or /review command was invoked, do nothing
    if config.event_name in ("issue_comment", "pull_request_review_comment", "pull_request_review") and cmd_info is None:
        logger.info("Comment or review body does not invoke a /tommi slash command. Nothing to do.")
        return

    # 1. Acknowledge the request with 👀 reaction
    commenter.add_reaction("eyes")

    # 2. Determine action based on event and command
    try:
        if config.event_name == "pull_request" and config.is_merged:
            # Post-Merge Review Learning Mode
            logger.info(f"Triggered post-merge rule learning on PR #{config.pr_number} ({config.github_repository})")
            reviewer_instance = TommiReviewer(config=config, auth_token=target_token)
            diff_text = reviewer_instance.fetch_pr_diff(pr.url)

            tommi_g = auth_manager.get_client_for_repo(config.tommi_repo)
            learner = TommiLearner(config=config, github_client=target_g, tommi_client=tommi_g)
            result = learner.learn_from_merged_pr(pr=pr, pr_diff=diff_text)

            if result and result.get("pr_url"):
                pr_url = result.get("pr_url")
                summary = result.get("learning_plan", {}).get("summary", "Rule update")
                response_msg = (
                    f"🎓 **T.O.M.M.I. Post-Merge Learning**\n\n"
                    f"I've analyzed maintainer review feedback from this merged PR (*{summary}*) and opened a rule proposal Pull Request on `thomasglasser/tommi`:\n"
                    f"👉 **[View Rule Proposal PR]({pr_url})**\n\n"
                    f"Once merged, this rule will automatically apply to all future reviews."
                )
                commenter.post_issue_comment(response_msg)
            return

        cmd_type, cmd_arg = cmd_info if cmd_info else ("", "")

        if cmd_type == "help":
            help_msg = (
                "🤖 **T.O.M.M.I. AI Assistant Commands**\n\n"
                "• `/tommi review` — Run automated code review against this PR\n"
                "• `/tommi false-positive <explanation>` — Report an inaccurate review comment to refine rules\n"
                "• `/tommi learn <rule>` — Teach a new coding standard or architectural rule\n"
                "• `/tommi help` — Display this command reference"
            )
            commenter.reply_to_comment(help_msg)
            return

        if cmd_type in ("learn", "false-positive"):
            feedback_text = cmd_arg
            if not feedback_text:
                commenter.reply_to_comment(
                    "⚠️ Please provide feedback or a rule description after `/tommi` (e.g. `/tommi <explanation>` or `/tommi learn <rule>`)."
                )
                return

            # Verify that comment author has write access to the central TOMMI rules repository
            tommi_g = auth_manager.get_client_for_repo(config.tommi_repo)
            tommi_repo = tommi_g.get_repo(config.tommi_repo)

            has_write_access = False
            if config.comment_author and isinstance(config.comment_author, str):
                try:
                    owner_login = tommi_repo.owner.login if hasattr(tommi_repo.owner, "login") and isinstance(tommi_repo.owner.login, str) else ""
                    if owner_login.lower() == config.comment_author.lower():
                        has_write_access = True
                    else:
                        perm = tommi_repo.get_collaborator_permission(config.comment_author)
                        if isinstance(perm, str) and perm.lower() in ("admin", "write", "maintain"):
                            has_write_access = True
                except Exception as perm_err:
                    logger.warning(f"Could not verify write permissions for '{config.comment_author}' on '{config.tommi_repo}': {perm_err}")

            if not has_write_access:
                logger.warning(f"Unauthorized feedback attempt by '{config.comment_author}' (no write access to '{config.tommi_repo}').")
                commenter.reply_to_comment(
                    f"🔒 **Permission Denied**: Modifying rules or reporting false positives requires write access to the central rules repository (`{config.tommi_repo}`). You can still use `/tommi review` to review this PR."
                )
                return

            reviewer_instance = TommiReviewer(config=config, auth_token=target_token)
            diff_text = reviewer_instance.fetch_pr_diff(pr.url)

            # Build rich thread context if replying to a code review comment
            thread_context_parts = []
            if config.file_path:
                thread_context_parts.append(f"File: `{config.file_path}`")
            if config.in_reply_to_id:
                try:
                    parent_comment = pr.get_comment(config.in_reply_to_id)
                    thread_context_parts.append(f"Original Review Comment by {parent_comment.user.login}:\n\"{parent_comment.body}\"")
                except Exception as e:
                    logger.warning(f"Could not fetch parent review comment #{config.in_reply_to_id}: {e}")
            if config.diff_hunk:
                thread_context_parts.append(f"Diff Hunk:\n```diff\n{config.diff_hunk}\n```")

            thread_context = "\n\n".join(thread_context_parts) if thread_context_parts else None

            tommi_g = auth_manager.get_client_for_repo(config.tommi_repo)
            learner = TommiLearner(config=config, github_client=target_g, tommi_client=tommi_g)
            result = learner.process_feedback(
                command_type=cmd_type,
                feedback_text=feedback_text,
                pr_title=pr.title,
                pr_diff=diff_text,
                thread_context=thread_context,
            )

            pr_url = result.get("pr_url")
            summary = result.get("learning_plan", {}).get("summary", "Rule update")

            response_msg = (
                f"🎓 **T.O.M.M.I. Feedback Processed!**\n\n"
                f"I've synthesized this feedback (*{summary}*) and opened a rule proposal Pull Request on `thomasglasser/tommi`:\n"
                f"👉 **[View Rule Proposal PR]({pr_url})**\n\n"
                f"Once merged, this rule will automatically apply to all future reviews."
            )
            commenter.reply_to_comment(response_msg)
            commenter.add_reaction("hooray")
            logger.info("Successfully processed learning feedback.")

        elif cmd_type == "review" or config.event_name == "pull_request":
            # Code Review Mode (Comment or Automatic on PR Event)
            reviewer = TommiReviewer(config=config, auth_token=target_token)
            comments = reviewer.review_pr(
                pr_title=pr.title,
                pr_body=pr.body or "",
                pr_url=pr.url,
            )

            if not comments:
                logger.info("No style violations or issues found.")
                commenter.add_reaction("rocket")
                commenter.post_issue_comment("✅ **T.O.M.M.I. Review**: Looks good! No rule violations or obvious bugs detected.")
            else:
                logger.info(f"Posting {len(comments)} review comments...")
                commenter.post_review_comments(comments)
                commenter.add_reaction("rocket")

        elif cmd_type == "unrecognized":
            help_msg = (
                "🤖 **T.O.M.M.I. AI Assistant Commands**\n\n"
                "• `/tommi review` — Run automated code review against this PR\n"
                "• `/tommi false-positive <explanation>` — Report an inaccurate review comment to refine rules\n"
                "• `/tommi learn <rule>` — Teach a new coding standard or architectural rule\n"
                "• `/tommi help` — Display this command reference"
            )
            commenter.reply_to_comment(help_msg)

        else:
            logger.info("No recognizable TOMMI command in comment body.")

    except QuotaExceededException as qe:
        logger.warning(f"Quota error: {qe}")
        quota_msg = "😴 **T.O.M.M.I. is resting:** I've run out of AI API quota for today. Please try again tomorrow."
        commenter.reply_to_comment(quota_msg)
    except HighDemandException as hde:
        logger.warning(f"High demand error: {hde}")
        demand_msg = "⏳ **T.O.M.M.I. is busy:** The AI model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again in a few moments."
        commenter.reply_to_comment(demand_msg)
    except Exception as e:
        error_str = str(e).lower()
        if "503" in error_str or "high demand" in error_str or "unavailable" in error_str or "overloaded" in error_str:
            logger.warning(f"High demand error: {e}")
            demand_msg = "⏳ **T.O.M.M.I. is busy:** The AI model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again in a few moments."
            commenter.reply_to_comment(demand_msg)
        elif "429" in error_str or "quota" in error_str or "exhausted" in error_str:
            logger.warning(f"Quota error: {e}")
            quota_msg = "😴 **T.O.M.M.I. is resting:** I've run out of AI API quota for today. Please try again tomorrow."
            commenter.reply_to_comment(quota_msg)
        elif "authentication available for repository" in error_str or "could not get app installation token" in error_str or "504" in error_str or "gateway" in error_str:
            logger.error(f"GitHub authentication / connectivity error: {e}")
            auth_msg = (
                f"⚠️ **T.O.M.M.I. GitHub Connection Error:** Unable to authenticate or communicate with the rules repository (`{config.tommi_repo}`). "
                "GitHub's API may be experiencing downtime or gateway timeouts. Please try running your command again in a moment."
            )
            commenter.reply_to_comment(auth_msg)
            commenter.add_reaction("confused")
            sys.exit(1)
        else:
            logger.error(f"Error during execution: {e}", exc_info=True)
            commenter.add_reaction("confused")
            sys.exit(1)


if __name__ == "__main__":
    main()
