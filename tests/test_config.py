from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_zcurve.config import (
    AppSettings,
    RunSettings,
    load_app_settings,
    load_run_settings,
    save_app_settings,
    save_run_settings,
)


class ConfigTests(unittest.TestCase):
    def test_legacy_settings_default_to_gemini(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            path = project / ".auto_zcurve" / "run_settings.json"
            path.parent.mkdir()
            path.write_text('{"primary_model":"legacy-model"}\n', encoding="utf-8")
            settings = load_run_settings(project)
            self.assertIsNotNone(settings)
            self.assertEqual(settings.provider, "gemini")
            self.assertEqual(settings.pdf_parser, "native")
            self.assertEqual(settings.reasoning_effort, "high")

    def test_gemini_provider_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            save_run_settings(
                project,
                RunSettings(primary_model="gemma-4-31b-it", provider="gemini"),
            )
            payload = json.loads(
                (project / ".auto_zcurve" / "run_settings.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["provider"], "gemini")
            self.assertEqual(payload["reasoning_effort"], "high")
            self.assertEqual(load_run_settings(project).provider, "gemini")

    def test_app_settings_default_to_native_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}, clear=False):
                defaults = load_app_settings()
                self.assertEqual(defaults.pdf_parser, "native")
                self.assertEqual(defaults.reasoning_effort, "high")
                self.assertEqual(defaults.parallel_requests, 1)
                self.assertEqual(defaults.request_delay_sec, 30)
                self.assertEqual(defaults.default_gemini_model, "gemini-3.6-flash")
                self.assertEqual(defaults.default_openrouter_model, "")

                save_app_settings(
                    AppSettings(
                        pdf_parser="cloudflare-ai",
                        parallel_requests=4,
                        request_delay_sec=45,
                        request_timeout_sec=900,
                        max_upload_size_mb=64,
                        reasoning_effort="low",
                        default_gemini_model="gemini-3.1-flash-lite",
                        default_openrouter_model="openai/gpt-5",
                    )
                )
                loaded = load_app_settings()

        self.assertEqual(loaded.pdf_parser, "cloudflare-ai")
        self.assertEqual(loaded.parallel_requests, 4)
        self.assertEqual(loaded.request_delay_sec, 45)
        self.assertEqual(loaded.request_timeout_sec, 900)
        self.assertEqual(loaded.max_upload_size_mb, 64)
        self.assertEqual(loaded.reasoning_effort, "low")
        self.assertEqual(loaded.default_gemini_model, "gemini-3.1-flash-lite")
        self.assertEqual(loaded.default_openrouter_model, "openai/gpt-5")


if __name__ == "__main__":
    unittest.main()
