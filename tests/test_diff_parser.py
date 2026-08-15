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
        self.assertFalse(parsed.is_line_in_diff(path, 999))
        self.assertEqual(parsed.get_closest_valid_line(path, 999), max(valid_lines))

if __name__ == "__main__":
    unittest.main()
