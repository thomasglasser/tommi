import argparse
import json
import logging
import os
import sys
from typing import List, Optional

from src.config import TommiConfig
from src.reviewer import TommiReviewer, QuotaExceededException, HighDemandException
from src.diff_parser import filter_diff_for_review
from src.local import (
    get_git_root,
    get_current_branch,
    extract_git_diff,
    format_terminal_review,
    format_markdown_report,
)

logger = logging.getLogger("tommi.cli")


class TommiArgumentParser(argparse.ArgumentParser):
    def parse_known_args(self, args=None, namespace=None):
        namespace, remaining = super().parse_known_args(args=args, namespace=namespace)
        if getattr(namespace, "paths", None) and namespace.paths and namespace.paths[0] == "review":
            namespace.paths.pop(0)
        return namespace, remaining


def build_parser() -> argparse.ArgumentParser:
    parser = TommiArgumentParser(
        prog="tommi",
        description="🤖 T.O.M.M.I. — AI-Powered Automated Code Reviewer (Local CLI)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  tommi review                      # Auto-detect: reviews uncommitted changes, or branch vs main
  tommi review --staged             # Review only staged (git add) changes
  tommi review --branch             # Review all branch commits compared to main / origin/main
  tommi review --base origin/main   # Review branch compared to origin/main
  tommi review --commit HEAD        # Review the last commit
  tommi review src/MyClass.java     # Review specific files
  tommi review -o report.md         # Save review report to a markdown file
  tommi review --fail-on warning    # Exit with code 1 if warnings or critical issues found
        """,
    )
    parser.set_defaults(command="review")
    _add_review_arguments(parser)
    return parser



def _add_review_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "paths",
        nargs="*",
        help="Optional file(s) or directories to restrict the review to.",
    )
    diff_group = p.add_argument_group("Diff Scope Options")
    diff_group.add_argument(
        "--staged", "--cached",
        action="store_true",
        help="Review only staged changes in the Git index (git diff --cached).",
    )
    diff_group.add_argument(
        "--unstaged",
        action="store_true",
        help="Review only unstaged changes in the working tree (git diff).",
    )
    diff_group.add_argument(
        "--working",
        action="store_true",
        help="Review all uncommitted changes, both staged and unstaged (git diff HEAD).",
    )
    diff_group.add_argument(
        "--branch",
        action="store_true",
        help="Review all commits on the current branch compared to base branch.",
    )
    diff_group.add_argument(
        "--base",
        default=None,
        help="Base branch/ref to compare against (e.g. main, origin/main). Default: auto-detected.",
    )
    diff_group.add_argument(
        "--commit", "-c",
        default=None,
        help="Review a specific Git commit hash or revision (e.g. HEAD, HEAD~1, abc1234).",
    )
    diff_group.add_argument(
        "--diff-file", "-f",
        default=None,
        help="Path to an existing unified diff or patch file to review.",
    )

    output_group = p.add_argument_group("Output & Reporting Options")
    output_group.add_argument(
        "--output", "-o",
        default=None,
        help="Write full markdown review report to the specified file path (e.g. review.md).",
    )
    output_group.add_argument(
        "--json",
        action="store_true",
        help="Output raw review findings as JSON to stdout.",
    )
    output_group.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output in terminal.",
    )
    output_group.add_argument(
        "--fail-on",
        choices=["critical", "warning", "suggestion", "never"],
        default="critical",
        help="Exit with non-zero status code if findings reach this severity threshold (default: critical).",
    )

    config_group = p.add_argument_group("Configuration & Model Options")
    config_group.add_argument(
        "--model", "-m",
        default="auto",
        help="Gemini model name override (default: 'auto', discovering fastest Flash model).",
    )
    config_group.add_argument(
        "--thinking-budget",
        type=int,
        default=2048,
        help="Thinking budget token count (default: 2048, 0 to disable).",
    )
    config_group.add_argument(
        "--dir", "-C",
        default=None,
        help="Working directory of target repository (default: current working directory).",
    )
    config_group.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging.",
    )


def execute_review(args: argparse.Namespace) -> int:
    target_dir = os.path.abspath(args.dir) if args.dir else os.getcwd()
    git_root = get_git_root(target_dir)

    if not git_root and not args.diff_file:
        print(f"Error: '{target_dir}' is not inside a Git repository, and no --diff-file was provided.", file=sys.stderr)
        return 1

    effective_root = git_root or target_dir
    repo_name = os.path.basename(effective_root)
    branch_name = get_current_branch(effective_root) if git_root else "diff"

    # Determine diff mode
    mode = "auto"
    if args.staged:
        mode = "staged"
    elif args.unstaged:
        mode = "unstaged"
    elif args.working:
        mode = "working"
    elif args.branch or args.base:
        mode = "branch"

    file_paths = args.paths if args.paths else None

    # Extract diff
    try:
        diff_text, diff_desc = extract_git_diff(
            git_root=effective_root,
            mode=mode,
            base=args.base,
            commit=args.commit,
            diff_file=args.diff_file,
            file_paths=file_paths,
        )
    except Exception as e:
        print(f"Error extracting git diff: {e}", file=sys.stderr)
        return 1

    if not diff_text or not diff_text.strip():
        print(f"No changes detected ({diff_desc}). Nothing to review!")
        return 0

    filtered = filter_diff_for_review(diff_text)
    if not filtered or not filtered.strip():
        print(f"Diff contains no reviewable code or configuration files ({diff_desc}). All changes are binary or ignored assets.")
        return 0

    # Build local config
    try:
        config = TommiConfig.for_local(
            model_name=args.model,
            thinking_budget=args.thinking_budget,
            workspace_dir=effective_root,
        )
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    # Execute review
    if not args.json:
        use_color = not args.no_color and sys.stdout.isatty()
        print(f"Analyzing {diff_desc} with T.O.M.M.I. rules engine...")

    reviewer = TommiReviewer(config=config, workspace_dir=effective_root)

    try:
        comments = reviewer.review_diff(
            diff_text=diff_text,
            title=f"Local Review: {diff_desc}",
            description=f"Local review of {repo_name} ({branch_name})",
            repo_workspace_dir=effective_root,
        )
    except QuotaExceededException:
        print("Error: Gemini API quota exceeded. Please check your API quota or retry later.", file=sys.stderr)
        return 1
    except HighDemandException:
        print("Error: Gemini models are currently experiencing high demand. Please retry in a few moments.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error during AI code review: {e}", file=sys.stderr)
        if args.verbose:
            logger.exception("Review failure details:")
        return 1

    unreviewed = getattr(reviewer, "unreviewed_files", [])

    # Output formatting
    if args.json:
        output_data = {
            "repository": repo_name,
            "branch": branch_name,
            "diff_scope": diff_desc,
            "findings_count": len(comments),
            "unreviewed_files": unreviewed,
            "comments": comments,
        }
        print(json.dumps(output_data, indent=2))
    else:
        use_color = not args.no_color and (sys.stdout.isatty() or os.name == "nt")
        terminal_output = format_terminal_review(
            comments=comments,
            diff_desc=diff_desc,
            repo_name=repo_name,
            branch_name=branch_name,
            model_name=config.model_name,
            unreviewed_files=unreviewed,
            use_color=use_color,
        )
        print(terminal_output)

    # Save to report file if requested
    if args.output:
        try:
            report_md = format_markdown_report(
                comments=comments,
                diff_desc=diff_desc,
                repo_name=repo_name,
                branch_name=branch_name,
                model_name=config.model_name,
                unreviewed_files=unreviewed,
            )
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report_md)
            print(f"Wrote review report to: {args.output}")
        except Exception as e:
            print(f"Failed to write report file '{args.output}': {e}", file=sys.stderr)

    # Determine exit code based on --fail-on
    if args.fail_on == "never":
        return 0

    has_critical = any(c.get("severity") == "CRITICAL" for c in comments)
    has_warning = any(c.get("severity") == "WARNING" for c in comments)
    has_suggestion = any(c.get("severity") == "SUGGESTION" for c in comments)

    if args.fail_on == "critical" and has_critical:
        return 1
    elif args.fail_on == "warning" and (has_critical or has_warning):
        return 1
    elif args.fail_on == "suggestion" and (has_critical or has_warning or has_suggestion):
        return 1

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Default to review command if none or 'review' given
    return execute_review(args)


if __name__ == "__main__":
    sys.exit(main())
