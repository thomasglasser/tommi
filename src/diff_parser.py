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
    line_contents: Dict[str, Dict[int, str]] = field(default_factory=dict)
    raw_diff: str = ""

    def is_line_in_diff(self, file_path: str, line_number: int) -> bool:
        """Returns True if the line number exists in the right side of the diff for this file."""
        return file_path in self.files and line_number in self.files[file_path]

    def get_closest_valid_line(self, file_path: str, target_line: int, max_distance: int = 3) -> Optional[int]:
        """Finds the closest valid line in the file diff if within max_distance lines."""
        if file_path not in self.files or not self.files[file_path]:
            return None
        valid_lines = sorted(self.files[file_path])
        closest = min(valid_lines, key=lambda l: abs(l - target_line))
        if abs(closest - target_line) <= max_distance:
            return closest
        return None

    def get_line_indent(self, file_path: str, line_number: int) -> str:
        """Returns the leading whitespace of the line, or empty string."""
        if file_path in self.line_contents and line_number in self.line_contents[file_path]:
            raw_line = self.line_contents[file_path][line_number]
            return raw_line[:len(raw_line) - len(raw_line.lstrip())]
        return ""

    def find_matching_line(self, file_path: str, target_snippet: str, preferred_line: Optional[int] = None) -> Optional[int]:
        """
        Finds the line number in the file diff that best matches target_snippet.
        Checks preferred_line first, then nearby lines (+/- 30), then the entire diff for that file.
        """
        if file_path not in self.line_contents or not self.line_contents[file_path]:
            return None

        clean_target = target_snippet.strip()
        if not clean_target:
            return None

        # If clean_target is multi-line, take the first non-empty line
        first_line_target = next((l.strip() for l in clean_target.splitlines() if l.strip()), clean_target)

        file_lines = self.line_contents[file_path]

        def _matches(content_str: str) -> bool:
            c = content_str.strip()
            if not c:
                return False
            if first_line_target in c:
                return True
            if len(c) >= 5 and c in first_line_target:
                return True
            ct_bare = first_line_target.rstrip(";{}(),.").strip()
            c_bare = c.rstrip(";{}(),.").strip()
            if len(ct_bare) >= 3 and len(c_bare) >= 3:
                if ct_bare in c_bare or c_bare in ct_bare:
                    return True
            if "=" in first_line_target and "=" in c:
                left_target = first_line_target.split("=")[0].strip()
                left_c = c.split("=")[0].strip()
                if len(left_target) >= 3 and left_target == left_c:
                    return True
            return False

        # 1. Direct match at preferred_line
        if preferred_line and preferred_line in file_lines:
            if _matches(file_lines[preferred_line]):
                return preferred_line

        # 2. Windowed search around preferred_line (+/- 30 lines)
        if preferred_line:
            candidates = sorted(file_lines.keys(), key=lambda l: abs(l - preferred_line))
            for line_num in candidates:
                if abs(line_num - preferred_line) > 30:
                    break
                if _matches(file_lines[line_num]):
                    return line_num

        # 3. Search throughout all diff lines for this file
        for line_num, content in file_lines.items():
            if _matches(content):
                return line_num

        return None


def parse_unified_diff(diff_text: str, filter_non_code: bool = True) -> ParsedDiff:
    """
    Parses a unified diff and extracts all valid new line numbers (RIGHT side) and line contents
    for each modified file. Optionally filters out lockfiles, non-code assets, and translations.
    """
    files: Dict[str, Set[int]] = {}
    line_contents: Dict[str, Dict[int, str]] = {}
    current_file: Optional[str] = None
    is_current_file_reviewable = True
    current_new_line = 0

    hunk_header_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
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
                    line_contents[current_file] = {}
                else:
                    current_file = None
            continue

        if current_file is None or not is_current_file_reviewable:
            continue

        hunk_match = hunk_header_re.match(line)
        if hunk_match:
            current_new_line = int(hunk_match.group(1))
            continue

        if line.startswith("+"):
            files[current_file].add(current_new_line)
            line_contents[current_file][current_new_line] = line[1:]
            current_new_line += 1
        elif line.startswith(" "):
            files[current_file].add(current_new_line)
            line_contents[current_file][current_new_line] = line[1:]
            current_new_line += 1
        elif line.startswith("-"):
            pass

    return ParsedDiff(files=files, line_contents=line_contents, raw_diff=diff_text)


def format_annotated_diff(diff_text: str) -> str:
    """
    Annotates unified diff lines with their exact 1-based line numbers in the right (new) side.
    Added lines: ' 189: + ...'
    Context lines: ' 187:   ...'
    Deleted lines: '     -: - ...'
    Headers: preserved as is.
    """
    annotated_lines = []
    current_new_line = 0
    in_hunk = False
    hunk_header_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for line in diff_text.splitlines():
        if line.startswith("diff --git ") or line.startswith("--- ") or line.startswith("+++ "):
            in_hunk = False
            annotated_lines.append(line)
            continue

        hunk_match = hunk_header_re.match(line)
        if hunk_match:
            current_new_line = int(hunk_match.group(1))
            in_hunk = True
            annotated_lines.append(line)
            continue

        if in_hunk:
            if line.startswith("+"):
                annotated_lines.append(f"{current_new_line:5d}: +{line[1:]}")
                current_new_line += 1
            elif line.startswith(" "):
                annotated_lines.append(f"{current_new_line:5d}:  {line[1:]}")
                current_new_line += 1
            elif line.startswith("-"):
                annotated_lines.append(f"     -: -{line[1:]}")
            else:
                annotated_lines.append(f"      : {line}")
        else:
            annotated_lines.append(line)

    return "\n".join(annotated_lines)


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

