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

if __name__ == "__main__":
    unittest.main()
