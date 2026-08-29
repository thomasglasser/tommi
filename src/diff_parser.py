import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

IGNORED_DIFF_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".jar", ".zip", ".gz", ".tar", ".bin",
    ".lock", ".lockfile", ".map", ".min.js", ".min.css",
}

IGNORED_DIFF_PATTERNS = [
    r"gradle\.lockfile$",
    r"package-lock\.json$",
    r"yarn\.lock$",
    r"pnpm-lock\.yaml$",
    r"assets/[^/]+/lang/[^/]+\.json$",
]


def is_reviewable_file(file_path: str) -> bool:
    """Returns True if the file should be reviewed by AI."""
    clean_path = file_path.replace("\\", "/").strip()
    ext = os.path.splitext(clean_path)[1].lower()
    if ext in IGNORED_DIFF_EXTENSIONS:
        return False
    for pat in IGNORED_DIFF_PATTERNS:
        if re.search(pat, clean_path, re.IGNORECASE):
            return False
    return True


@dataclass
class FileDiffHunk:
    new_start: int
    new_count: int
    lines: List[str] = field(default_factory=list)
    valid_new_lines: Set[int] = field(default_factory=set)


@dataclass
class ParsedDiff:
    files: Dict[str, Set[int]] = field(default_factory=dict)
    raw_diff: str = ""

    def is_line_in_diff(self, file_path: str, line_number: int) -> bool:
        """Returns True if the line number exists in the right side of the diff for this file."""
        return file_path in self.files and line_number in self.files[file_path]

    def get_closest_valid_line(self, file_path: str, target_line: int) -> Optional[int]:
        """Finds the closest valid line in the file diff if the exact line is slightly off."""
        if file_path not in self.files or not self.files[file_path]:
            return None
        valid_lines = sorted(self.files[file_path])
        return min(valid_lines, key=lambda l: abs(l - target_line))


def parse_unified_diff(diff_text: str, filter_non_code: bool = True) -> ParsedDiff:
    """
    Parses a unified diff and extracts all valid new line numbers (RIGHT side) for each modified file.
    Optionally filters out lockfiles, non-code assets, and translations.
    """
    files: Dict[str, Set[int]] = {}
    current_file: Optional[str] = None
    is_current_file_reviewable = True
    current_new_line = 0

    hunk_header_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            # Example: diff --git a/path/to/file.java b/path/to/file.java
            parts = line.split(" ")
            if len(parts) >= 4:
                b_path = parts[3]
                if b_path.startswith("b/"):
                    current_file = b_path[2:]
                else:
                    current_file = b_path

                is_current_file_reviewable = not filter_non_code or is_reviewable_file(current_file)
                if is_current_file_reviewable:
                    files[current_file] = set()
                else:
                    current_file = None
            continue

        if current_file is None or not is_current_file_reviewable:
            continue

        # Check for hunk header
        hunk_match = hunk_header_re.match(line)
        if hunk_match:
            current_new_line = int(hunk_match.group(1))
            continue

        # Check line diff markers
        if line.startswith("+"):
            files[current_file].add(current_new_line)
            current_new_line += 1
        elif line.startswith(" "):
            # Context line on both sides
            files[current_file].add(current_new_line)
            current_new_line += 1
        elif line.startswith("-"):
            # Deletion on left side; new line counter does not advance
            pass

    return ParsedDiff(files=files, raw_diff=diff_text)


def filter_diff_for_review(diff_text: str) -> str:
    """
    Strips non-code and ignored file sections from unified diff to minimize token consumption.
    """
    filtered_chunks = []
    current_chunk = []
    include_current = True

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if current_chunk and include_current:
                filtered_chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            parts = line.split(" ")
            if len(parts) >= 4:
                b_path = parts[3]
                path = b_path[2:] if b_path.startswith("b/") else b_path
                include_current = is_reviewable_file(path)
            else:
                include_current = True
        else:
            if current_chunk is not None:
                current_chunk.append(line)

    if current_chunk and include_current:
        filtered_chunks.append("\n".join(current_chunk))

    return "\n".join(filtered_chunks)
