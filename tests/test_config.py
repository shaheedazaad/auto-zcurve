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
    load_selected_extractor_id,
    save_app_settings,
    save_run_settings,
    save_selected_extractor_id,
    selected_extractor_path,
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

    def test_run_settings_record_the_extractor_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            snapshot = {"name": "Local", "provider": "openai_compatible", "model": "qwen", "base_url": "http://h/v1",
                        "response_format": "none", "context_length": 32000, "parallel_requests": 4,
                        "request_delay_sec": 2, "request_timeout_sec": 90}
            save_run_settings(project, RunSettings(
                primary_model="qwen", provider="openai_compatible", max_upload_size_mb=12,
                extractor_id="abc", extractor=snapshot,
            ))
            payload = json.loads((project / ".auto_zcurve" / "run_settings.json").read_text(encoding="utf-8"))
            self.assertEqual(payload, {"extractor_id": "abc", "extractor": snapshot, "pdf_parser": "native", "max_upload_size_mb": 12})

            loaded = load_run_settings(project)
            self.assertEqual((loaded.provider, loaded.primary_model, loaded.extractor_id), ("openai_compatible", "qwen", "abc"))
            self.assertEqual((loaded.base_url, loaded.response_format, loaded.context_length), ("http://h/v1", "none", 32000))
            self.assertEqual((loaded.parallel_requests, loaded.request_delay_sec, loaded.request_timeout_sec), (4, 2, 90))
            self.assertEqual(loaded.extractor, snapshot)

    def test_run_settings_without_a_snapshot_keep_their_concrete_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            settings = RunSettings(
                primary_model="vendor/model", provider="openrouter", request_timeout_sec=120,
                parallel_requests=3, request_delay_sec=7, reasoning_effort="low",
                openrouter_provider="deepinfra", openrouter_allow_fallbacks=False,
            )
            save_run_settings(project, settings)
            loaded = load_run_settings(project)
            for name in ("request_timeout_sec", "parallel_requests", "request_delay_sec", "reasoning_effort",
                         "openrouter_provider", "openrouter_allow_fallbacks"):
                self.assertEqual(getattr(loaded, name), getattr(settings, name), name)

    def test_legacy_flat_run_settings_still_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            path = project / ".auto_zcurve" / "run_settings.json"
            path.parent.mkdir()
            path.write_text(json.dumps({
                "primary_model": "vendor/model", "provider": "openrouter", "parallel_requests": "bad",
                "openrouter_provider": " DeepInfra/FP8 ", "openrouter_allow_fallbacks": False, "request_delay_sec": 9999,
            }), encoding="utf-8")
            loaded = load_run_settings(project)
            self.assertEqual((loaded.openrouter_provider, loaded.openrouter_allow_fallbacks), ("deepinfra/fp8", False))
            self.assertEqual((loaded.parallel_requests, loaded.request_delay_sec, loaded.extractor_id), (1, 3600, ""))
            self.assertIsNone(loaded.extractor)

    def test_selected_extractor_is_remembered_per_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self.assertEqual(load_selected_extractor_id(project), "")
            save_selected_extractor_id(project, "abc")
            self.assertEqual(load_selected_extractor_id(project), "abc")
            selected_extractor_path(project).write_text("[]", encoding="utf-8")
            self.assertEqual(load_selected_extractor_id(project), "")
            selected_extractor_path(project).write_text("{broken", encoding="utf-8")
            self.assertEqual(load_selected_extractor_id(project), "")

    def test_app_settings_default_to_native_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}, clear=False):
                self.assertEqual(load_app_settings(), AppSettings(pdf_parser="native", max_upload_size_mb=128, retry_extractor_change="rerun_all"))
                save_app_settings(AppSettings(pdf_parser="cloudflare-ai", max_upload_size_mb=64, retry_extractor_change="keep_results"))
                loaded = load_app_settings()
                stored = json.loads(Path(tmp, "auto-zcurve", "settings.json").read_text(encoding="utf-8"))

        self.assertEqual(loaded, AppSettings(pdf_parser="cloudflare-ai", max_upload_size_mb=64, retry_extractor_change="keep_results"))
        self.assertEqual(stored, {"pdf_parser": "cloudflare-ai", "max_upload_size_mb": 64, "retry_extractor_change": "keep_results"})

    def test_an_unknown_retry_choice_falls_back_to_rerunning_everything(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}, clear=False):
            save_app_settings(AppSettings(retry_extractor_change="sometimes"))
            self.assertEqual(load_app_settings().retry_extractor_change, "rerun_all")
            save_app_settings(AppSettings(retry_extractor_change=" Keep_Results "))
            self.assertEqual(load_app_settings().retry_extractor_change, "keep_results")

    def test_an_unreadable_last_run_counts_as_no_previous_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            path = project / ".auto_zcurve" / "run_settings.json"
            path.parent.mkdir()
            for contents in ("{broken", "[]"):
                with self.subTest(contents=contents):
                    path.write_text(contents, encoding="utf-8")
                    self.assertIsNone(load_run_settings(project))


if __name__ == "__main__":
    unittest.main()
