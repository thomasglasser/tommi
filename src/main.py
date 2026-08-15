import logging
import sys
from github import Github

from src.config import TommiConfig
from src.github_auth import GitHubAuthManager
from src.commenter import GitHubCommenter
from src.reviewer import TommiReviewer, QuotaExceededException
from src.learner import TommiLearner

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("tommi")


def main():
    try:
        config = TommiConfig.from_env()
    except Exception as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

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

    # 1. Acknowledge the request with 👀 reaction
    commenter.add_reaction("eyes")

    comment_body = config.comment_body.strip()
    pr = commenter.pr

    # 2. Determine action based on event and comment
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

        if "/tommi learn" in comment_body or "/tommi false-positive" in comment_body:
            # Learning / Feedback Mode
            if "/tommi learn" in comment_body:
                cmd_type = "learn"
                feedback_text = comment_body.split("/tommi learn", 1)[1].strip()
            else:
                cmd_type = "false-positive"
                feedback_text = comment_body.split("/tommi false-positive", 1)[1].strip()

            if not feedback_text:
                commenter.post_issue_comment(
                    "⚠️ Please provide feedback or a rule description after `/tommi learn` or `/tommi false-positive`."
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

        elif "/tommi review" in comment_body or "/review" in comment_body:
            # Code Review Mode
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

        else:
            logger.info("No recognizable TOMMI command in comment body.")

    except QuotaExceededException as qe:
        logger.warning(f"Quota error: {qe}")
        quota_msg = "😴 **T.O.M.M.I. is resting:** I've run out of AI API quota for today. Please try again tomorrow."
        commenter.post_issue_comment(quota_msg)
        commenter.add_reaction("confused")
    except Exception as e:
        logger.error(f"Error during execution: {e}", exc_info=True)
        commenter.add_reaction("confused")
        sys.exit(1)


if __name__ == "__main__":
    main()
