import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_zcurve.models import (
    ModelOption,
    _apply_model_allowlist,
    _configured_models,
    validate_model_option,
)


class ModelConfigTests(unittest.TestCase):
    def test_reads_gemini_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.yml"
            path.write_text(
                "models:\n"
                "  gemini:\n"
                "    - gemini-3.6-flash\n"
                "    - gemma-4-31b-it\n",
                encoding="utf-8",
            )
            configured = _configured_models(path)

        self.assertEqual(configured["gemini"]["gemini-3.6-flash"], ((), 1, 30))
        self.assertEqual(configured["gemini"]["gemma-4-31b-it"], ((), 1, 30))

    def test_allowlist_filters_gemini_catalog(self):
        configured = {"gemini": {"allowed-model": ((), 1, 30)}}
        options = [
            ModelOption("allowed-model", "Allowed"),
            ModelOption("blocked-model", "Blocked"),
        ]
        with patch("auto_zcurve.models._configured_models", return_value=configured):
            filtered = _apply_model_allowlist("gemini", options)

        self.assertEqual([option.name for option in filtered], ["allowed-model"])
        self.assertEqual(filtered[0].endpoint_order, ())

    def test_gemini_model_outside_allowlist_is_rejected(self):
        with patch(
            "auto_zcurve.models._configured_models",
            return_value={"gemini": {"gemini-3.6-flash": ()}},
        ):
            with self.assertRaisesRegex(ValueError, "not allowed"):
                validate_model_option("gemini", "gemini-unlisted", "key")

    def test_invalid_endpoint_shape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.yml"
            path.write_text(
                "models:\n"
                "  gemini:\n"
                "    - id: allowed-model\n"
                "      endpoints: invalid\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must be a list"):
                _configured_models(path)


if __name__ == "__main__":
    unittest.main()
