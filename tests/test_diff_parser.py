import unittest
from src.diff_parser import parse_unified_diff

SAMPLE_DIFF = """diff --git a/src/main/java/com/example/MyClass.java b/src/main/java/com/example/MyClass.java
index 1234567..89abcdef 100644
--- a/src/main/java/com/example/MyClass.java
+++ b/src/main/java/com/example/MyClass.java
@@ -10,6 +10,8 @@ package com.example;
 import java.util.List;
 
 public class MyClass {
+    public static final String FOO = "bar";
+
     public void tick() {
-        int a = 1;
+        int a = 2;
     }
 }
"""

class TestDiffParser(unittest.TestCase):
    def test_parse_unified_diff(self):
        parsed = parse_unified_diff(SAMPLE_DIFF)
        path = "src/main/java/com/example/MyClass.java"
        
        self.assertIn(path, parsed.files)
        valid_lines = parsed.files[path]

        # Lines 10 (package), 11 (import), 12 (empty), 13 (public class), 14 (+FOO), 15 (+empty), 16 (public void tick), 17 (+int a = 2)
        self.assertIn(14, valid_lines)
        self.assertIn(15, valid_lines)
        self.assertIn(17, valid_lines)
        
        self.assertTrue(parsed.is_line_in_diff(path, 14))
    def test_is_reviewable_file(self):
        from src.diff_parser import is_reviewable_file
        self.assertTrue(is_reviewable_file("src/main/java/MyClass.java"))
        self.assertTrue(is_reviewable_file("src/main/resources/data/mod/recipes/craft.json"))
        self.assertFalse(is_reviewable_file("gradle.lockfile"))
        self.assertFalse(is_reviewable_file("package-lock.json"))
        self.assertFalse(is_reviewable_file("assets/mod/lang/en_us.json"))
        self.assertFalse(is_reviewable_file("assets/mod/textures/item/tool.png"))

    def test_filter_diff_for_review(self):
        from src.diff_parser import filter_diff_for_review
        raw = (
            "diff --git a/src/Test.java b/src/Test.java\n+class Test {}\n"
            "diff --git a/gradle.lockfile b/gradle.lockfile\n+lock content\n"
            "diff --git a/assets/mod/lang/en_us.json b/assets/mod/lang/en_us.json\n+translation\n"
        )
        filtered = filter_diff_for_review(raw)
        self.assertIn("src/Test.java", filtered)
        self.assertNotIn("gradle.lockfile", filtered)
        self.assertNotIn("en_us.json", filtered)

    def test_format_annotated_diff(self):
        from src.diff_parser import format_annotated_diff
        annotated = format_annotated_diff(SAMPLE_DIFF)
        path = "src/main/java/com/example/MyClass.java"
        # Check that line numbers are annotated on right-side lines
        self.assertIn("   10:  package com.example;", annotated)
        self.assertIn("   14: +    public static final String FOO = \"bar\";", annotated)
        self.assertIn("     -: -        int a = 1;", annotated)
        self.assertIn("   17: +        int a = 2;", annotated)

    def test_line_contents_and_indent(self):
        parsed = parse_unified_diff(SAMPLE_DIFF)
        path = "src/main/java/com/example/MyClass.java"
        self.assertEqual(parsed.get_line_indent(path, 14), "    ")
        self.assertEqual(parsed.get_line_indent(path, 17), "        ")
        self.assertEqual(parsed.line_contents[path][14].strip(), "public static final String FOO = \"bar\";")

    def test_find_matching_line(self):
        parsed = parse_unified_diff(SAMPLE_DIFF)
        path = "src/main/java/com/example/MyClass.java"

        # Direct match at line 14
        self.assertEqual(parsed.find_matching_line(path, "public static final String FOO = \"bar\";", preferred_line=14), 14)

        # Drifted preferred_line (AI guessed line 20, but the line is at line 14)
        self.assertEqual(parsed.find_matching_line(path, "public static final String FOO = \"bar\";", preferred_line=20), 14)

        # Substring / partial match
        self.assertEqual(parsed.find_matching_line(path, "int a = 2;", preferred_line=10), 17)

    def test_get_closest_valid_line_distance_threshold(self):
        parsed = parse_unified_diff(SAMPLE_DIFF)
        path = "src/main/java/com/example/MyClass.java"

        # Line 18 is 1 line away from 17 -> should snap
        self.assertEqual(parsed.get_closest_valid_line(path, 18, max_distance=3), 17)

        # Line 50 is far away -> should return None
        self.assertIsNone(parsed.get_closest_valid_line(path, 50, max_distance=3))


if __name__ == "__main__":
    unittest.main()
