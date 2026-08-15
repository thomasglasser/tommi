import os
import unittest
from unittest.mock import patch
from src.config import TommiConfig

class TestConfig(unittest.TestCase):
    @patch.dict(os.environ, {
        "GITHUB_TOKEN": "ghp_test123",
        "GEMINI_API_KEY": "gemini_test456",
        "GITHUB_REPOSITORY": "thomasglasser/Mineraculous",
        "PR_NUMBER": "42",
        "COMMENT_ID": "1001",
        "COMMENT_BODY": "/tommi review",
        "MODEL_NAME": "gemini-2.5-flash",
        "STRICTNESS": "strict",
        "TOMMI_REPO": "thomasglasser/tommi"
    })
    def test_valid_config_from_env(self):
        cfg = TommiConfig.from_env()
        self.assertEqual(cfg.github_token, "ghp_test123")
        self.assertEqual(cfg.gemini_api_key, "gemini_test456")
        self.assertEqual(cfg.github_repository, "thomasglasser/Mineraculous")
        self.assertEqual(cfg.pr_number, 42)
        self.assertEqual(cfg.comment_id, 1001)
        self.assertEqual(cfg.comment_body, "/tommi review")
        self.assertEqual(cfg.model_name, "gemini-2.5-flash")
        self.assertEqual(cfg.strictness, "strict")
        self.assertEqual(cfg.tommi_repo, "thomasglasser/tommi")

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_config_raises(self):
        with self.assertRaises(ValueError):
            TommiConfig.from_env()

if __name__ == "__main__":
    unittest.main()
