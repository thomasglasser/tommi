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
        self.assertIn("   10:  import java.util.List;", annotated)
        self.assertIn("   13: +    public static final String FOO = \"bar\";", annotated)
        self.assertIn("     -: -        int a = 1;", annotated)
        self.assertIn("   16: +        int a = 2;", annotated)

    def test_line_contents_and_indent(self):
        parsed = parse_unified_diff(SAMPLE_DIFF)
        path = "src/main/java/com/example/MyClass.java"
        self.assertEqual(parsed.get_line_indent(path, 13), "    ")
        self.assertEqual(parsed.get_line_indent(path, 16), "        ")
        self.assertEqual(parsed.line_contents[path][13].strip(), "public static final String FOO = \"bar\";")
        self.assertEqual(parsed.line_contents[path][16].strip(), "int a = 2;")

    def test_find_matching_line(self):
        parsed = parse_unified_diff(SAMPLE_DIFF)
        path = "src/main/java/com/example/MyClass.java"

        # Direct match at line 13
        self.assertEqual(parsed.find_matching_line(path, "public static final String FOO = \"bar\";", preferred_line=13), 13)

        # Drifted preferred_line (AI guessed line 20, but the line is at line 13)
        self.assertEqual(parsed.find_matching_line(path, "public static final String FOO = \"bar\";", preferred_line=20), 13)

        # Substring / partial match
        self.assertEqual(parsed.find_matching_line(path, "int a = 2;", preferred_line=10), 16)

    def test_get_closest_valid_line_distance_threshold(self):
        parsed = parse_unified_diff(SAMPLE_DIFF)
        path = "src/main/java/com/example/MyClass.java"

        # Line 20 is 2 lines away from 18 (last line) -> should snap to 18
        self.assertEqual(parsed.get_closest_valid_line(path, 20, max_distance=3), 18)

        # Line 50 is far away -> should return None
        self.assertIsNone(parsed.get_closest_valid_line(path, 50, max_distance=3))

    def test_ignored_patterns_and_extensions(self):
        from src.diff_parser import is_reviewable_file
        # Generated code
        self.assertFalse(is_reviewable_file("src/generated/resources/data/tags.json"))
        # Asset models, animations, geo, textures, sounds, shaders
        self.assertFalse(is_reviewable_file("assets/mod/animations/entity.animation.json"))
        self.assertFalse(is_reviewable_file("assets/mod/geo/entity.geo.json"))
        self.assertFalse(is_reviewable_file("assets/mod/models/item/wand.json"))
        self.assertFalse(is_reviewable_file("assets/mod/sounds/sound.ogg"))
        self.assertFalse(is_reviewable_file("assets/mod/textures/block.png.mcmeta"))
        # Valid code and data
        self.assertTrue(is_reviewable_file("src/main/java/com/example/Foo.java"))
        self.assertTrue(is_reviewable_file("src/main/resources/data/mineraculous/recipes/foo.json"))

    def test_split_diff_into_batches(self):
        from src.diff_parser import split_diff_into_batches

        # Empty diff
        self.assertEqual(split_diff_into_batches(""), [])
        self.assertEqual(split_diff_into_batches("   "), [])

        # Single file diff
        batches = split_diff_into_batches(SAMPLE_DIFF, max_files_per_batch=5)
        self.assertEqual(len(batches), 1)
        self.assertIn("MyClass.java", batches[0])

        # Multi-file diff exceeding max_files_per_batch
        multi_diff = (
            "diff --git a/File1.java b/File1.java\n+1\n"
            "diff --git a/File2.java b/File2.java\n+2\n"
            "diff --git a/File3.java b/File3.java\n+3\n"
        )
        batches_files = split_diff_into_batches(multi_diff, max_files_per_batch=2)
        self.assertEqual(len(batches_files), 2)
        self.assertIn("File1.java", batches_files[0])
        self.assertIn("File2.java", batches_files[0])
        self.assertIn("File3.java", batches_files[1])

        # Multi-file diff exceeding max_chars_per_batch
        batches_chars = split_diff_into_batches(multi_diff, max_files_per_batch=10, max_chars_per_batch=40)
        self.assertGreater(len(batches_chars), 1)


if __name__ == "__main__":
    unittest.main()

