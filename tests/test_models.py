import unittest

from auto_zcurve.models import fallback_models


class ModelTests(unittest.TestCase):
    def test_fallback_models_are_latest_main_models(self):
        self.assertEqual(
            [model.name for model in fallback_models()],
            ["gemini-3.6-flash", "gemini-3.1-pro-preview", "gemini-3-flash-preview"],
        )


if __name__ == "__main__":
    unittest.main()
