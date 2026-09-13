import unittest
from src.diff_parser import parse_unified_diff

MULTI_FILE_DIFF = """diff --git a/src/First.java b/src/First.java
index 1111111..2222222 100644
--- a/src/First.java
+++ b/src/First.java
@@ -5,3 +5,4 @@ public class First {
     int x = 1;
+    int y = 2;
 }
diff --git a/src/Second.java b/src/Second.java
index 3333333..4444444 100644
--- a/src/Second.java
+++ b/src/Second.java
@@ -1,4 +1,3 @@
-import java.util.stream.Stream;
 public class Second {
 }
"""

class TestDiffEdgeCases(unittest.TestCase):
    def test_multi_file_diff(self):
        parsed = parse_unified_diff(MULTI_FILE_DIFF)
        self.assertIn("src/First.java", parsed.files)
        self.assertIn("src/Second.java", parsed.files)
        
        self.assertTrue(parsed.is_line_in_diff("src/First.java", 6))
        # Second.java only had a deletion; line 1 is "public class Second {" (context line)
        self.assertTrue(parsed.is_line_in_diff("src/Second.java", 1))

    def test_empty_diff(self):
        parsed = parse_unified_diff("")
        self.assertEqual(len(parsed.files), 0)

    def test_diff_header_does_not_add_line_zero(self):
        parsed = parse_unified_diff(MULTI_FILE_DIFF)
        self.assertNotIn(0, parsed.files["src/First.java"])
        self.assertNotIn(0, parsed.files["src/Second.java"])

    def test_diff_paths_with_spaces_and_quotes(self):
        from src.diff_parser import extract_diff_git_path, filter_diff_for_review

        diff_with_spaces = """diff --git a/src/path with space/My Class.java b/src/path with space/My Class.java
index 1111111..2222222 100644
--- a/src/path with space/My Class.java
+++ b/src/path with space/My Class.java
@@ -1,2 +1,3 @@
 public class MyClass {
+    int val = 42;
 }
"""
        parsed = parse_unified_diff(diff_with_spaces)
        expected_path = "src/path with space/My Class.java"
        self.assertIn(expected_path, parsed.files)
        self.assertTrue(parsed.is_line_in_diff(expected_path, 2))
        self.assertNotIn(0, parsed.files[expected_path])

        # Quoted path
        quoted_header = 'diff --git "a/src/path with space/Quoted Class.java" "b/src/path with space/Quoted Class.java"'
        self.assertEqual(extract_diff_git_path(quoted_header), "src/path with space/Quoted Class.java")

        # Filter diff preserves reviewable files with spaces
        filtered = filter_diff_for_review(diff_with_spaces)
        self.assertIn("src/path with space/My Class.java", filtered)


if __name__ == "__main__":
    unittest.main()
