import os
import unittest
import tempfile
from src.rules_loader import load_all_rules

class TestRulesLoader(unittest.TestCase):
    def test_load_base_and_local_rules(self):
        with tempfile.TemporaryDirectory() as tmp_workspace:
            # Create a mock AGENTS.md in the workspace
            agents_md = os.path.join(tmp_workspace, "AGENTS.md")
            with open(agents_md, "w", encoding="utf-8") as f:
                f.write("# Project Rules\n* ALWAYS use custom holders.\n")

            # Create a mock TOMMI.md in the workspace
            tommi_md = os.path.join(tmp_workspace, "TOMMI.md")
            with open(tommi_md, "w", encoding="utf-8") as f:
                f.write("# Extra Tommi Rules\n* Do not use raw IDs.\n")

            rules_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules")
            loaded = load_all_rules(repo_workspace_dir=tmp_workspace, tommi_rules_dir=rules_dir)

            self.assertIn("core", loaded.base_rules)
            self.assertIn("java", loaded.base_rules)
            self.assertIn("minecraft", loaded.base_rules)
            self.assertIn("performance", loaded.base_rules)
            self.assertIn("AGENTS.md", loaded.local_rules)
            self.assertIn("TOMMI.md", loaded.local_rules)

            formatted = loaded.format_for_prompt()
            self.assertIn("GLOBAL STYLE & ARCHITECTURE RULES", formatted)
            self.assertIn("REPOSITORY-SPECIFIC RULES", formatted)
            self.assertIn("ALWAYS use custom holders", formatted)

            # Assert base rule hierarchy: MINECRAFT -> PERFORMANCE -> JAVA -> CORE
            idx_minecraft = formatted.index("[MINECRAFT]")
            idx_perf = formatted.index("[PERFORMANCE]")
            idx_java = formatted.index("[JAVA]")
            idx_core = formatted.index("[CORE]")
            self.assertTrue(idx_minecraft < idx_perf < idx_java < idx_core)

if __name__ == "__main__":
    unittest.main()
