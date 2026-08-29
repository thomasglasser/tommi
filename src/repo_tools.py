import fnmatch
import os
import re
from typing import List, Optional, Dict, Any

# Directories to ignore during recursive search
IGNORED_DIRS = {
    ".git", ".gradle", "build", "out", "target", "bin", ".idea", ".vscode",
    "node_modules", "dist", ".gemini", "__pycache__", ".pytest_cache"
}

# Default file extensions to consider code files
CODE_EXTENSIONS = {
    ".java", ".kt", ".scala", ".groovy", ".json", ".xml", ".yml", ".yaml",
    ".md", ".toml", ".properties", ".gradle", ".py", ".ts", ".js"
}


class WorkspaceInspector:
    """
    Provides safe, sandboxed inspection of the checked-out repository workspace.
    Used by T.O.M.M.I. to look up method declarations, return types, class definitions,
    and constants during PR reviews.
    """

    def __init__(self, workspace_dir: Optional[str] = None):
        raw_dir = workspace_dir or os.environ.get("GITHUB_WORKSPACE", os.getcwd())
        self.workspace_dir = os.path.abspath(raw_dir)

    def _resolve_safe_path(self, relative_path: str) -> Optional[str]:
        """
        Resolves a relative path within the workspace, preventing directory traversal.
        """
        clean_rel = relative_path.strip().lstrip("/\\")
        full_path = os.path.abspath(os.path.join(self.workspace_dir, clean_rel))
        # Ensure path stays within workspace_dir
        if full_path == self.workspace_dir or full_path.startswith(self.workspace_dir + os.sep):
            return full_path
        return None

    def read_file(self, file_path: str, start_line: int = 1, end_line: int = 150, max_lines: int = 200) -> str:
        """
        Reads a specific range of lines from a file in the workspace repository.
        
        Args:
            file_path: The relative path to the file from the repository root (e.g. 'src/main/java/com/mod/MyClass.java').
            start_line: The 1-based start line number (default: 1).
            end_line: The 1-based end line number (inclusive).
            max_lines: Maximum number of lines allowed in a single slice (default: 200).
        """
        target = self._resolve_safe_path(file_path)
        if not target or not os.path.isfile(target):
            return f"Error: File '{file_path}' not found in workspace."

        start_line = max(1, start_line)
        end_line = max(start_line, min(start_line + max_lines, end_line))

        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            return f"Error reading file '{file_path}': {e}"

        total_lines = len(lines)
        if start_line > total_lines:
            return f"File '{file_path}' has {total_lines} lines (requested start line {start_line} is out of bounds)."

        slice_lines = lines[start_line - 1:end_line]
        formatted = []
        for i, line in enumerate(slice_lines, start=start_line):
            formatted.append(f"{i:4d}: {line.rstrip()}")

        header = f"=== File: {file_path} (Lines {start_line}-{min(end_line, total_lines)} of {total_lines}) ==="
        return f"{header}\n" + "\n".join(formatted)

    def get_hunk_context(self, file_path: str, changed_lines: Optional[List[int]] = None, padding: int = 40) -> str:
        """
        Reads the surrounding context for changed lines in a file.
        If the entire file is <= 400 lines, returns the complete file.
        Otherwise, returns merged window slices around changed lines with line numbers.
        """
        target = self._resolve_safe_path(file_path)
        if not target or not os.path.isfile(target):
            return f"Error: File '{file_path}' not found in workspace."

        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            return f"Error reading file '{file_path}': {e}"

        total_lines = len(lines)
        if total_lines == 0:
            return f"=== File: {file_path} (Empty file) ==="

        # If file is small or no specific changed lines provided, return the whole file
        if total_lines <= 400 or not changed_lines:
            formatted = [f"{i:4d}: {line.rstrip()}" for i, line in enumerate(lines, start=1)]
            header = f"=== File: {file_path} (Lines 1-{total_lines} of {total_lines}) ==="
            return f"{header}\n" + "\n".join(formatted)

        # Merge overlapping ranges around changed lines
        ranges = []
        for line_num in sorted(changed_lines):
            start = max(1, line_num - padding)
            end = min(total_lines, line_num + padding)
            if not ranges:
                ranges.append([start, end])
            else:
                last_start, last_end = ranges[-1]
                if start <= last_end + 5:
                    ranges[-1][1] = max(last_end, end)
                else:
                    ranges.append([start, end])

        # Format merged slices
        sections = []
        for start, end in ranges:
            slice_lines = lines[start - 1:end]
            formatted = [f"{i:4d}: {line.rstrip()}" for i, line in enumerate(slice_lines, start=start)]
            header = f"--- {file_path} (Lines {start}-{end} of {total_lines}) ---"
            sections.append(f"{header}\n" + "\n".join(formatted))

        return f"=== File Context: {file_path} ===\n" + "\n\n".join(sections)

    def find_files(self, pattern: str) -> str:
        """
        Finds files in the workspace matching a glob pattern (e.g. '*Attachment*.java', '*Manager*').
        
        Args:
            pattern: Glob pattern to match against file paths or names.
        """
        matches = []
        clean_pat = pattern.strip()
        if not clean_pat.startswith("*") and not clean_pat.endswith("*"):
            clean_pat = f"*{clean_pat}*"

        for root, dirs, files in os.walk(self.workspace_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for file in files:
                rel_path = os.path.relpath(os.path.join(root, file), self.workspace_dir).replace("\\", "/")
                if fnmatch.fnmatch(file, clean_pat) or fnmatch.fnmatch(rel_path, clean_pat):
                    matches.append(rel_path)
                    if len(matches) >= 50:
                        break
            if len(matches) >= 50:
                break

        if not matches:
            return f"No files matching pattern '{pattern}' found in workspace."
        
        return f"Found {len(matches)} matching file(s):\n" + "\n".join(f"- {p}" for p in matches)

    def search_codebase(self, query: str, file_pattern: str = "*.java") -> str:
        """
        Searches the workspace codebase for a keyword, symbol, or regex query.
        
        Args:
            query: The search term or regex pattern (e.g. 'public static .* get(', 'class ModAttachments').
            file_pattern: Optional file glob pattern to filter search (default: '*.java').
        """
        if not query or not query.strip():
            return "Error: Search query cannot be empty."

        try:
            regex = re.compile(query, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(query), re.IGNORECASE)

        results = []
        for root, dirs, files in os.walk(self.workspace_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for file in files:
                if file_pattern and not fnmatch.fnmatch(file, file_pattern):
                    continue
                ext = os.path.splitext(file)[1].lower()
                if not file_pattern and ext not in CODE_EXTENSIONS:
                    continue

                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.workspace_dir).replace("\\", "/")

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        for line_num, line in enumerate(f, start=1):
                            if regex.search(line):
                                results.append(f"{rel_path}:{line_num}: {line.strip()}")
                                if len(results) >= 40:
                                    break
                except Exception:
                    continue

                if len(results) >= 40:
                    break
            if len(results) >= 40:
                break

        if not results:
            return f"No occurrences of '{query}' found in files matching '{file_pattern}'."

        return f"Found {len(results)} match(es) for '{query}':\n" + "\n".join(results)

    def get_symbol_definition(self, symbol_name: str) -> str:
        """
        Searches for a class, method, or record definition in the workspace and returns its declaration and surrounding body.
        
        Args:
            symbol_name: The name of the class, record, interface, or method (e.g. 'AbilityContext', 'getOrCreate', 'getLevel').
        """
        clean_name = symbol_name.strip()
        if not clean_name:
            return "Error: Symbol name cannot be empty."

        patterns = [
            re.compile(rf"\b(class|interface|enum|record)\s+{re.escape(clean_name)}\b"),
            re.compile(rf"\b[A-Za-z0-9_<>\[\]]+\s+{re.escape(clean_name)}\s*\("),
            re.compile(rf"\b{re.escape(clean_name)}\s*\("),
        ]

        found_snippets = []
        for root, dirs, files in os.walk(self.workspace_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext not in {".java", ".kt", ".scala"}:
                    continue

                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.workspace_dir).replace("\\", "/")

                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                except Exception:
                    continue

                for line_idx, line in enumerate(lines):
                    for pat in patterns:
                        if pat.search(line):
                            start = max(0, line_idx - 2)
                            end = min(len(lines), line_idx + 25)
                            snippet = "\n".join(f"{i+1:4d}: {lines[i].rstrip()}" for i in range(start, end))
                            found_snippets.append(f"=== {rel_path} (around line {line_idx+1}) ===\n{snippet}")
                            if len(found_snippets) >= 5:
                                break
                    if len(found_snippets) >= 5:
                        break
            if len(found_snippets) >= 5:
                break

        if not found_snippets:
            return f"Symbol '{symbol_name}' declaration not found. Try searching with `search_codebase`."

        return "\n\n".join(found_snippets)

    def get_tool_callables(self) -> Dict[str, Any]:
        """
        Returns a mapping of tool names to callable methods for Gemini tool execution.
        """
        return {
            "read_file": self.read_file,
            "find_files": self.find_files,
            "search_codebase": self.search_codebase,
            "get_symbol_definition": self.get_symbol_definition,
        }
