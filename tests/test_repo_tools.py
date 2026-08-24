import os
import tempfile
import unittest
from src.repo_tools import WorkspaceInspector


class TestWorkspaceInspector(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = self.temp_dir.name

        # Create sample directory structure and code files
        self.java_dir = os.path.join(self.workspace, "src", "main", "java", "com", "example")
        os.makedirs(self.java_dir, exist_ok=True)

        self.sample_java_file = os.path.join(self.java_dir, "MyAttachment.java")
        with open(self.sample_java_file, "w", encoding="utf-8") as f:
            f.write(
                "package com.example;\n"
                "\n"
                "import net.minecraft.world.entity.LivingEntity;\n"
                "\n"
                "public class MyAttachment {\n"
                "    public static final int DURATION_TICKS = 200;\n"
                "\n"
                "    public static MyAttachment get(LivingEntity entity) {\n"
                "        return entity.getData(ModAttachments.MY_ATTACHMENT);\n"
                "    }\n"
                "}\n"
            )

        self.inspector = WorkspaceInspector(workspace_dir=self.workspace)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_file_success(self):
        result = self.inspector.read_file("src/main/java/com/example/MyAttachment.java", start_line=5, end_line=10)
        self.assertIn("=== File: src/main/java/com/example/MyAttachment.java (Lines 5-10 of 11) ===", result)
        self.assertIn("public class MyAttachment", result)
        self.assertIn("public static MyAttachment get", result)

    def test_read_file_not_found(self):
        result = self.inspector.read_file("src/NonExistent.java")
        self.assertIn("Error: File 'src/NonExistent.java' not found in workspace.", result)

    def test_read_file_path_traversal_blocked(self):
        result = self.inspector.read_file("../../outside.txt")
        self.assertIn("Error: File '../../outside.txt' not found in workspace.", result)

    def test_find_files(self):
        result = self.inspector.find_files("MyAttachment")
        self.assertIn("Found 1 matching file(s):", result)
        self.assertIn("src/main/java/com/example/MyAttachment.java", result)

        not_found = self.inspector.find_files("NonExistentPattern")
        self.assertIn("No files matching pattern", not_found)

    def test_search_codebase(self):
        result = self.inspector.search_codebase("DURATION_TICKS")
        self.assertIn("Found 1 match(es) for 'DURATION_TICKS':", result)
        self.assertIn("src/main/java/com/example/MyAttachment.java:6: public static final int DURATION_TICKS = 200;", result)

    def test_get_symbol_definition(self):
        result = self.inspector.get_symbol_definition("MyAttachment")
        self.assertIn("MyAttachment.java", result)
        self.assertIn("public class MyAttachment", result)

        method_result = self.inspector.get_symbol_definition("get")
        self.assertIn("public static MyAttachment get", method_result)


if __name__ == "__main__":
    unittest.main()
