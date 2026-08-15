import glob
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class LoadedRules:
    base_rules: Dict[str, str] = field(default_factory=dict)
    local_rules: Dict[str, str] = field(default_factory=dict)

    def format_for_prompt(self) -> str:
        sections = []

        # 1. Base Rules from TOMMI
        sections.append("### GLOBAL STYLE & ARCHITECTURE RULES:")
        for rule_name, rule_content in sorted(self.base_rules.items()):
            sections.append(f"#### [{rule_name.upper()}]\n{rule_content.strip()}\n")

        # 2. Local Repository Rules (TOMMI.md / AGENTS.md)
        if self.local_rules:
            sections.append("### REPOSITORY-SPECIFIC RULES (from project TOMMI.md / AGENTS.md):")
            for source_name, rule_content in sorted(self.local_rules.items()):
                sections.append(f"#### [{source_name}]\n{rule_content.strip()}\n")

        return "\n".join(sections)


def load_all_rules(repo_workspace_dir: Optional[str] = None, tommi_rules_dir: Optional[str] = None) -> LoadedRules:
    """
    Loads base rules from TOMMI's rules directory and local rules from the target repository workspace.
    """
    base_rules: Dict[str, str] = {}
    local_rules: Dict[str, str] = {}

    # 1. Resolve TOMMI base rules directory
    if not tommi_rules_dir:
        # Default: rules/ directory alongside src/ or parent
        possible_dirs = [
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules"),
            os.path.join(os.getcwd(), "rules"),
            "/rules"
        ]
        for p in possible_dirs:
            if os.path.isdir(p):
                tommi_rules_dir = p
                break

    if tommi_rules_dir and os.path.isdir(tommi_rules_dir):
        for rule_file in glob.glob(os.path.join(tommi_rules_dir, "*.md")):
            name = os.path.splitext(os.path.basename(rule_file))[0]
            try:
                with open(rule_file, "r", encoding="utf-8") as f:
                    base_rules[name] = f.read()
            except Exception as e:
                print(f"Warning: Failed to read base rule file {rule_file}: {e}")

    # 2. Resolve target repository workspace directory
    workspace = repo_workspace_dir or os.environ.get("GITHUB_WORKSPACE", os.getcwd())

    # Candidate file locations for local project rules
    local_candidates = [
        ("TOMMI.md", os.path.join(workspace, "TOMMI.md")),
        (".tommi.md", os.path.join(workspace, ".tommi.md")),
        (".github/tommi.md", os.path.join(workspace, ".github", "tommi.md")),
        (".github/TOMMI.md", os.path.join(workspace, ".github", "TOMMI.md")),
        ("AGENTS.md", os.path.join(workspace, "AGENTS.md")),
        (".agents/AGENTS.md", os.path.join(workspace, ".agents", "AGENTS.md")),
        ("GEMINI.md", os.path.join(workspace, "GEMINI.md")),
    ]

    for label, candidate_path in local_candidates:
        if os.path.isfile(candidate_path):
            try:
                with open(candidate_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        local_rules[label] = content
            except Exception as e:
                print(f"Warning: Failed to read local rule file {candidate_path}: {e}")

    # Check for .agents/rules/*.md
    agents_rules_pattern = os.path.join(workspace, ".agents", "rules", "*.md")
    for rule_file in glob.glob(agents_rules_pattern):
        rel_name = os.path.relpath(rule_file, workspace)
        try:
            with open(rule_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    local_rules[rel_name] = content
        except Exception as e:
            print(f"Warning: Failed to read local agent rule {rule_file}: {e}")

    return LoadedRules(base_rules=base_rules, local_rules=local_rules)
