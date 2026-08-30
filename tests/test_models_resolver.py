import unittest
from unittest.mock import MagicMock
from src.models_resolver import resolve_model_name, resolve_candidate_models

class MockModel:
    def __init__(self, name):
        self.name = name

class TestModelsResolver(unittest.TestCase):
    def test_explicit_model_name(self):
        client = MagicMock()
        resolved = resolve_model_name(client, configured_model="gemini-custom-model")
        self.assertEqual(resolved, "gemini-custom-model")

        candidates = resolve_candidate_models(client, configured_model="gemini-custom-model")
        self.assertEqual(candidates, ["gemini-custom-model"])

    def test_auto_defaults_to_3_7_flash(self):
        client = MagicMock()
        resolved = resolve_model_name(client, configured_model="auto")
        self.assertEqual(resolved, "gemini-3.7-flash")

        candidates = resolve_candidate_models(client, configured_model="auto")
        self.assertEqual(candidates[0], "gemini-3.7-flash")
        self.assertIn("gemini-3.5-flash", candidates)

if __name__ == "__main__":
    unittest.main()

