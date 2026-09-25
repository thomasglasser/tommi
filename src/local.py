import os
import subprocess
import sys
from typing import List, Dict, Any, Optional, Tuple

from src.config import TommiConfig
from src.reviewer import TommiReviewer, QuotaExceededException, HighDemandException
from src.diff_parser import parse_unified_diff, filter_diff_for_review


class AnsiColor:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    GRAY = "\033[90m"
    MAGENTA = "\033[95m"


def get_git_root(path: Optional[str] = None) -> Optional[str]:
    """Finds the root directory of the Git repository for the given path."""
    try:
        cmd = ["git"]
        if path:
            cmd.extend(["-C", path])
        cmd.extend(["rev-parse", "--show-toplevel"])
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except Exception:
        return None


def get_current_branch(git_root: str) -> str:
    """Returns the current active branch name or 'HEAD'."""
    try:
        res = subprocess.run(
            ["git", "-C", git_root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "HEAD"


def get_default_branch(git_root: str) -> Optional[str]:
    """
    Detects the default base branch for the repository.
    Checks:
    1. Symbolic ref of origin/HEAD (e.g. refs/remotes/origin/26.1 -> 26.1 or origin/26.1)
    2. Abbreviated ref of origin/HEAD
    3. Prefix of current branch (e.g. '26.1-smol-update' -> '26.1' or 'origin/26.1')
    4. Common candidate names ('origin/main', 'main', 'origin/master', 'master', etc.)
    """
    # 1. Try querying remote HEAD symbolic ref (e.g. refs/remotes/origin/26.1)
    try:
        res = subprocess.run(
            ["git", "-C", git_root, "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            ref = res.stdout.strip()
            if ref.startswith("refs/remotes/"):
                candidate = ref[len("refs/remotes/"):]
                local_candidate = candidate.replace("origin/", "")
                # Prefer local branch if it exists, otherwise remote ref
                res_local = subprocess.run(
                    ["git", "-C", git_root, "rev-parse", "--verify", local_candidate],
                    capture_output=True,
                    check=False,
                )
                if res_local.returncode == 0:
                    return local_candidate
                return candidate
    except Exception:
        pass

    # 2. Try git rev-parse --abbrev-ref origin/HEAD
    try:
        res = subprocess.run(
            ["git", "-C", git_root, "rev-parse", "--abbrev-ref", "origin/HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            candidate = res.stdout.strip()
            if candidate != "origin/HEAD":
                local_candidate = candidate.replace("origin/", "")
                res_local = subprocess.run(
                    ["git", "-C", git_root, "rev-parse", "--verify", local_candidate],
                    capture_output=True,
                    check=False,
                )
                if res_local.returncode == 0:
                    return local_candidate
                return candidate
    except Exception:
        pass

    # 3. Try current branch prefix (e.g. '26.1-feature' or '1.21.1/feature' -> '26.1' or '1.21.1')
    current = get_current_branch(git_root)
    if current and ("-" in current or "/" in current):
        prefix = current.split("-")[0].split("/")[0]
        for c in [prefix, f"origin/{prefix}"]:
            try:
                res = subprocess.run(
                    ["git", "-C", git_root, "rev-parse", "--verify", c],
                    capture_output=True,
                    check=False,
                )
                if res.returncode == 0:
                    return c
            except Exception:
                pass

    # 4. Standard candidates
    candidates = [
        "origin/main", "main",
        "origin/master", "master",
        "origin/trunk", "trunk",
        "origin/develop", "develop",
        "origin/dev", "dev",
    ]
    for c in candidates:
        try:
            res = subprocess.run(
                ["git", "-C", git_root, "rev-parse", "--verify", c],
                capture_output=True,
                check=False,
            )
            if res.returncode == 0:
                return c
        except Exception:
            pass

    return None


def has_uncommitted_changes(git_root: str) -> bool:
    """Returns True if the working directory or staging index has uncommitted modifications."""
    try:
        res = subprocess.run(
            ["git", "-C", git_root, "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(res.stdout.strip())
    except Exception:
        return False


def extract_git_diff(
    git_root: str,
    mode: str = "auto",
    base: Optional[str] = None,
    commit: Optional[str] = None,
    diff_file: Optional[str] = None,
    file_paths: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """
    Extracts the unified diff according to the selected mode.
    Returns (diff_text, description).
    """
    if diff_file:
        if not os.path.isfile(diff_file):
            raise FileNotFoundError(f"Diff file not found: {diff_file}")
        with open(diff_file, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), f"File: {os.path.basename(diff_file)}"

    if commit:
        cmd = ["git", "-C", git_root, "show", commit]
        desc = f"Commit: {commit}"
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout, desc
        except subprocess.CalledProcessError as e:
            err = (e.stderr or e.stdout or "").strip()
            raise RuntimeError(f"Git show failed: {err if err else str(e)}") from e

    # Base git command
    cmd = ["git", "-C", git_root, "diff"]
    path_args = ["--"] + file_paths if file_paths else []

    if mode == "staged":
        full_cmd = cmd + ["--cached"] + path_args
        desc = "Staged changes (--staged)"
    elif mode == "unstaged":
        full_cmd = cmd + path_args
        desc = "Unstaged working tree changes"
    elif mode == "branch":
        base_ref = base or get_default_branch(git_root)
        if not base_ref:
            raise ValueError(
                "Could not auto-detect a default base branch. "
                "Please specify the base branch explicitly using --base <ref> (e.g. tommi review --base 26.1)."
            )
        verify_res = subprocess.run(
            ["git", "-C", git_root, "rev-parse", "--verify", base_ref],
            capture_output=True,
            check=False,
        )
        if verify_res.returncode != 0:
            raise ValueError(
                f"Base branch '{base_ref}' does not exist in this repository. "
                "Please specify a valid base branch with --base <ref>."
            )
        full_cmd = cmd + [f"{base_ref}...HEAD"] + path_args
        desc = f"Branch changes against '{base_ref}'"
    elif mode == "working":
        full_cmd = cmd + ["HEAD"] + path_args
        desc = "Working changes (staged + unstaged)"
    else:  # mode == "auto"
        if file_paths:
            full_cmd = cmd + ["HEAD"] + path_args
            desc = f"Files: {', '.join(file_paths[:3])}{'...' if len(file_paths) > 3 else ''}"
        elif has_uncommitted_changes(git_root):
            full_cmd = cmd + ["HEAD"]
            desc = "Uncommitted changes (staged + unstaged)"
        else:
            current_branch = get_current_branch(git_root)
            default_branch = get_default_branch(git_root)

            has_valid_base = False
            if default_branch and current_branch != "HEAD":
                if default_branch != current_branch and default_branch.replace("origin/", "") != current_branch:
                    verify_res = subprocess.run(
                        ["git", "-C", git_root, "rev-parse", "--verify", default_branch],
                        capture_output=True,
                        check=False,
                    )
                    if verify_res.returncode == 0:
                        has_valid_base = True

            if has_valid_base:
                full_cmd = cmd + [f"{default_branch}...HEAD"]
                desc = f"Branch '{current_branch}' vs '{default_branch}'"
            else:
                full_cmd = ["git", "-C", git_root, "diff", "HEAD~1..HEAD"]
                desc = "Latest commit (HEAD)"

    try:
        res = subprocess.run(full_cmd, capture_output=True, text=True, check=True)
        return res.stdout, desc
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or "").strip()
        raise RuntimeError(f"Git diff failed: {err if err else str(e)}") from e



def format_terminal_review(
    comments: List[Dict[str, Any]],
    diff_desc: str,
    repo_name: str,
    branch_name: str,
    model_name: str,
    unreviewed_files: Optional[List[str]] = None,
    use_color: bool = True,
) -> str:
    """Formats the review findings for clean terminal output."""
    c = AnsiColor if use_color else None

    def color(text: str, col: str) -> str:
        return f"{col}{text}{c.RESET}" if c else text

    lines = []
    bar = "=" * 78
    thin_bar = "-" * 78

    lines.append(color(bar, AnsiColor.CYAN if c else ""))
    lines.append(color("🤖 T.O.M.M.I. Local Code Review", AnsiColor.BOLD if c else ""))
    lines.append(color(bar, AnsiColor.CYAN if c else ""))
    lines.append(f"  {color('Repository:', AnsiColor.DIM if c else '')} {repo_name} ({color(branch_name, AnsiColor.BOLD if c else '')})")
    lines.append(f"  {color('Diff Scope:', AnsiColor.DIM if c else '')} {diff_desc}")
    lines.append(f"  {color('AI Model:', AnsiColor.DIM if c else '')}   {model_name}")
    lines.append(color(thin_bar, AnsiColor.GRAY if c else ""))

    if unreviewed_files:
        lines.append(color(f"⚠️  Partial Review: {len(unreviewed_files)} file(s) skipped due to rate limits: {', '.join(unreviewed_files[:5])}", AnsiColor.YELLOW if c else ""))
        lines.append("")

    if not comments:
        lines.append("")
        lines.append(color("✨ Looks clean! No rule violations or obvious bugs detected.", AnsiColor.GREEN + (AnsiColor.BOLD if c else "") if c else ""))
        lines.append("")
        lines.append(color(bar, AnsiColor.CYAN if c else ""))
        return "\n".join(lines)

    # Count severities
    crit_count = sum(1 for item in comments if item.get("severity") == "CRITICAL")
    warn_count = sum(1 for item in comments if item.get("severity") == "WARNING")
    sugg_count = sum(1 for item in comments if item.get("severity") == "SUGGESTION")

    scorecard_parts = []
    if crit_count:
        scorecard_parts.append(color(f"🚨 {crit_count} Critical", AnsiColor.RED + (AnsiColor.BOLD if c else "") if c else ""))
    if warn_count:
        scorecard_parts.append(color(f"⚠️  {warn_count} Warning{'s' if warn_count != 1 else ''}", AnsiColor.YELLOW + (AnsiColor.BOLD if c else "") if c else ""))
    if sugg_count:
        scorecard_parts.append(color(f"💡 {sugg_count} Suggestion{'s' if sugg_count != 1 else ''}", AnsiColor.CYAN + (AnsiColor.BOLD if c else "") if c else ""))

    scorecard_str = " | ".join(scorecard_parts)
    lines.append(f"Findings: {scorecard_str}")
    lines.append("")

    # Group comments by file
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for item in comments:
        f_path = item.get("path", "unknown")
        by_file.setdefault(f_path, []).append(item)

    for file_path, items in by_file.items():
        lines.append(color(f"📄 {file_path} ({len(items)} finding{'s' if len(items) != 1 else ''})", AnsiColor.BOLD if c else ""))
        for it in items:
            line_num = it.get("line", "?")
            sev = it.get("severity", "WARNING")
            body = it.get("body", "").strip()

            if sev == "CRITICAL":
                sev_badge = color("🚨 [CRITICAL]", AnsiColor.RED + (AnsiColor.BOLD if c else "") if c else "")
            elif sev == "WARNING":
                sev_badge = color("⚠️  [WARNING]", AnsiColor.YELLOW + (AnsiColor.BOLD if c else "") if c else "")
            else:
                sev_badge = color("💡 [SUGGESTION]", AnsiColor.CYAN + (AnsiColor.BOLD if c else "") if c else "")

            # Strip existing prefix if body has it
            for pfx in ("**[CRITICAL]**", "**[WARNING]**", "**[SUGGESTION]**", "[CRITICAL]", "[WARNING]", "[SUGGESTION]"):
                if body.startswith(pfx):
                    body = body[len(pfx):].strip()
                    break

            lines.append(f"  Line {line_num} {sev_badge}")
            # Indent body
            for b_line in body.splitlines():
                lines.append(f"    {b_line}")
            lines.append("")

    lines.append(color(bar, AnsiColor.CYAN if c else ""))
    summary_line = f"Summary: {len(comments)} issue(s) found across {len(by_file)} file(s)."
    lines.append(color(summary_line, AnsiColor.BOLD if c else ""))
    lines.append(color(bar, AnsiColor.CYAN if c else ""))
    return "\n".join(lines)


def format_markdown_report(
    comments: List[Dict[str, Any]],
    diff_desc: str,
    repo_name: str,
    branch_name: str,
    model_name: str,
    unreviewed_files: Optional[List[str]] = None,
) -> str:
    """Formats the review findings as a GitHub Flavored Markdown report."""
    crit_count = sum(1 for item in comments if item.get("severity") == "CRITICAL")
    warn_count = sum(1 for item in comments if item.get("severity") == "WARNING")
    sugg_count = sum(1 for item in comments if item.get("severity") == "SUGGESTION")

    scorecard_parts = []
    if crit_count:
        scorecard_parts.append(f"🚨 **{crit_count} Critical**")
    if warn_count:
        scorecard_parts.append(f"⚠️ **{warn_count} Warning{'s' if warn_count != 1 else ''}**")
    if sugg_count:
        scorecard_parts.append(f"💡 **{sugg_count} Suggestion{'s' if sugg_count != 1 else ''}**")

    scorecard_str = " | ".join(scorecard_parts) if scorecard_parts else "✨ Clean"

    md = [
        "### 🤖 T.O.M.M.I. Local Code Review Report\n",
        f"- **Repository**: `{repo_name}` (`{branch_name}`)",
        f"- **Diff Scope**: {diff_desc}",
        f"- **Model**: `{model_name}`",
        f"- **Review Findings**: {scorecard_str}\n",
    ]

    if unreviewed_files:
        md.append(f"> ⚠️ **Partial Review Notice**: {len(unreviewed_files)} file(s) could not be reviewed due to rate limits: {', '.join(f'`{f}`' for f in unreviewed_files[:5])}\n")

    if not comments:
        md.append("✅ **Looks good!** No rule violations or obvious bugs detected.")
        return "\n".join(md)

    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for item in comments:
        f_path = item.get("path", "unknown")
        by_file.setdefault(f_path, []).append(item)

    for f_path, items in by_file.items():
        md.append(f"#### 📄 `{f_path}`\n")
        for it in items:
            line_num = it.get("line", "?")
            sev = it.get("severity", "WARNING")
            body = it.get("body", "").strip()
            # Strip duplicate prefixes
            for pfx in ("**[CRITICAL]**", "**[WARNING]**", "**[SUGGESTION]**", "[CRITICAL]", "[WARNING]", "[SUGGESTION]"):
                if body.startswith(pfx):
                    body = body[len(pfx):].strip()
                    break
            md.append(f"- **Line {line_num}** [{sev}]:\n\n  {body}\n")

    return "\n".join(md)
