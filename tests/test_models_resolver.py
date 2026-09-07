import unittest
from unittest.mock import MagicMock
from src.models_resolver import resolve_model_name, resolve_candidate_models, clear_model_cache

class MockModel:
    def __init__(self, name):
        self.name = name

class TestModelsResolver(unittest.TestCase):
    def setUp(self):
        clear_model_cache()

    def tearDown(self):
        clear_model_cache()

    def test_explicit_model_name(self):
        client = MagicMock()
        resolved = resolve_model_name(client, configured_model="gemini-custom-model")
        self.assertEqual(resolved, "gemini-custom-model")

        candidates = resolve_candidate_models(client, configured_model="gemini-custom-model")
        self.assertEqual(candidates, ["gemini-custom-model"])

    def test_auto_dynamic_discovery_and_sorting(self):
        client = MagicMock()
        client.models.list.return_value = [
            MockModel("models/gemini-2.0-flash"),
            MockModel("models/gemini-2.5-flash"),
            MockModel("models/gemini-3.7-flash"),
            MockModel("models/gemini-3.8-flash"),
            MockModel("models/gemini-3.8-flash-lite"),
            MockModel("models/gemini-2.0-flash-lite"),
            MockModel("models/gemini-1.5-flash-8b"),
            MockModel("models/text-embedding-004"),
            MockModel("models/gemini-2.0-flash-thinking-exp"),
        ]

        candidates = resolve_candidate_models(client, configured_model="auto")
        self.assertEqual(
            candidates,
            [
                "gemini-3.8-flash",
                "gemini-3.7-flash",
                "gemini-2.5-flash",
            ],
        )

        resolved = resolve_model_name(client, configured_model="auto")
        self.assertEqual(resolved, "gemini-3.8-flash")

    def test_auto_fails_if_models_list_fails(self):
        client = MagicMock()
        client.models.list.side_effect = RuntimeError("API connection timeout")

        with self.assertRaises(RuntimeError):
            resolve_candidate_models(client, configured_model="auto")

    def test_auto_fails_if_no_flash_models_found(self):
        client = MagicMock()
        client.models.list.return_value = [
            MockModel("models/text-embedding-004"),
            MockModel("models/gemini-embedding-001"),
        ]

        with self.assertRaises(ValueError):
            resolve_candidate_models(client, configured_model="auto")

if __name__ == "__main__":
    unittest.main()

