import unittest

from auto_zcurve.models import ModelOption, fallback_models, resolve_input_mode


class ModelTests(unittest.TestCase):
    def test_fallback_models_are_latest_main_models(self):
        self.assertEqual(
            [model.name for model in fallback_models()],
            [
                "gemini-3.6-flash",
                "gemini-3.1-pro-preview",
                "gemini-3-flash-preview",
                "gemma-4-31b-it",
                "gemma-4-26b-a4b-it",
            ],
        )


if __name__ == "__main__":
    unittest.main()
