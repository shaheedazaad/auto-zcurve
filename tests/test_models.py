import unittest

from auto_zcurve.models import fallback_models


class ModelTests(unittest.TestCase):
    def test_fallback_models_are_latest_main_models(self):
        self.assertEqual(
            [model.name for model in fallback_models()],
            ["gemini-3.1-pro-preview", "gemini-3.5-flash"],
        )


if __name__ == "__main__":
    unittest.main()
