from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from auto_zcurve import extractors as ex


class ExtractorTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config_dir = Path(self._tmp.name) / "auto-zcurve"
        self._env = patch.dict(os.environ, {"XDG_CONFIG_HOME": self._tmp.name})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def stored(self) -> dict:
        return json.loads((self.config_dir / "extractors.json").read_text(encoding="utf-8"))


class ExtractorDefinitionTests(ExtractorTestCase):
    def test_created_extractor_keeps_only_its_providers_fields(self):
        created = ex.create_extractor({
            "name": " Lab Gemini ",
            "provider": "gemini",
            "model": "models/gemini-3.6-flash",
            "service_tier": "priority",
            "base_url": "http://ignored.example/v1",
            "openrouter_provider": "ignored",
            "parallel_requests": "3",
            "request_delay_sec": "5",
            "request_timeout_sec": "120",
        })

        self.assertEqual(created.name, "Lab Gemini")
        self.assertEqual(created.model, "gemini-3.6-flash")
        self.assertEqual(ex.load_extractors(), [created])
        stored = self.stored()["extractors"][0]
        self.assertEqual(stored["service_tier"], "priority")
        self.assertEqual((stored["parallel_requests"], stored["request_delay_sec"], stored["request_timeout_sec"]), (3, 5, 120))
        for absent in ("base_url", "openrouter_provider", "response_format", "reasoning_effort", "api_key"):
            self.assertNotIn(absent, stored)
        if os.name == "posix":  # Windows has no owner-only file mode.
            self.assertEqual((self.config_dir / "extractors.json").stat().st_mode & 0o777, 0o600)

    def test_invalid_definitions_are_rejected_with_a_reason(self):
        ex.create_extractor({"name": "Main", "provider": "gemini", "model": "gemini-3.6-flash"})
        cases = {
            "already exists": {"name": "MAIN", "provider": "gemini", "model": "m"},
            "name": {"name": "  ", "provider": "gemini", "model": "m"},
            "model": {"name": "No model", "provider": "openrouter", "model": ""},
            "base URL": {"name": "Local", "provider": "openai_compatible", "model": "m"},
            "between 1 and 32": {"name": "Busy", "provider": "gemini", "model": "m", "parallel_requests": "40"},
            "whole number": {"name": "Slow", "provider": "gemini", "model": "m", "request_delay_sec": "soon"},
            "Unsupported LLM provider": {"name": "X", "provider": "anthropic", "model": "m"},
            "batch": {"name": "Batch", "provider": "openrouter", "model": "vendor/model:batch"},
            "at most 60": {"name": "x" * 61, "provider": "gemini", "model": "m"},
            "Invalid OpenRouter provider": {"name": "Route", "provider": "openrouter", "model": "v/m", "openrouter_provider": "bad slug!"},
            "Unsupported service tier": {"name": "Tier", "provider": "gemini", "model": "m", "service_tier": "turbo"},
            "Maximum output must be between": {"name": "Out", "provider": "gemini", "model": "m", "max_output_tokens": "0"},
        }
        for reason, values in cases.items():
            with self.subTest(reason=reason), self.assertRaisesRegex(ex.ExtractorError, reason):
                ex.create_extractor(values)
        self.assertEqual(len(ex.load_extractors()), 1)

    def test_update_changes_settings_but_never_the_provider(self):
        created = ex.create_extractor({
            "name": "Router", "provider": "openrouter", "model": "vendor/model",
            "openrouter_provider": "deepinfra/fp8", "openrouter_allow_fallbacks": "deny",
        })
        self.assertFalse(created.openrouter_allow_fallbacks)

        updated = ex.update_extractor(created.id, {
            "name": "Router B", "provider": "gemini", "model": "vendor/other",
            "reasoning_effort": "low", "openrouter_allow_fallbacks": "allow",
        })

        self.assertEqual(updated.id, created.id)
        self.assertEqual(updated.provider, "openrouter")
        self.assertEqual((updated.name, updated.model, updated.reasoning_effort), ("Router B", "vendor/other", "low"))
        self.assertEqual(updated.openrouter_provider, "")
        self.assertTrue(updated.openrouter_allow_fallbacks)
        self.assertEqual(ex.get_extractor(created.id), updated)
        with self.assertRaisesRegex(ex.ExtractorError, "no longer exists"):
            ex.update_extractor("missing", {"name": "x", "model": "y"})

    def test_duplicate_copies_settings_but_not_the_key(self):
        original = ex.create_extractor({"name": "Lab", "provider": "gemini", "model": "gemini-3.6-flash", "service_tier": "standard"})
        ex.set_key_saved(original.id, True)

        copy = ex.duplicate_extractor(original.id)
        second = ex.duplicate_extractor(original.id)

        self.assertEqual((copy.name, second.name), ("Lab copy", "Lab copy 2"))
        self.assertNotEqual(copy.id, original.id)
        self.assertEqual((copy.model, copy.service_tier), ("gemini-3.6-flash", "standard"))
        self.assertFalse(copy.key_saved)
        self.assertTrue(ex.get_extractor(original.id).key_saved)

    def test_removing_a_key_drops_an_unmoved_old_key_claim(self):
        created = ex.create_extractor({"name": "Old", "provider": "gemini", "model": "m"})
        ex.save_extractors([ex.replace(created, legacy_credential="gemini_api_key")])
        ex.set_key_saved(created.id, False)
        self.assertEqual(ex.get_extractor(created.id).legacy_credential, "")
        self.assertEqual(ex.legacy_claims(), {})

    def test_repeated_copies_of_a_long_name_stay_within_the_limit(self):
        original = ex.create_extractor({"name": "L" * ex.NAME_MAX_LENGTH, "provider": "gemini", "model": "m"})
        names = [ex.duplicate_extractor(original.id).name for _ in range(101)]
        self.assertTrue(all(len(name) <= ex.NAME_MAX_LENGTH for name in names))
        self.assertEqual(names[-1], "L" * (ex.NAME_MAX_LENGTH - 9) + " copy 101")
        self.assertEqual(len(ex.load_extractors()), 102)

    def test_concurrent_changes_are_all_kept(self):
        from concurrent.futures import ThreadPoolExecutor

        first = ex.create_extractor({"name": "Base", "provider": "gemini", "model": "m"})
        jobs = [lambda i=i: ex.create_extractor({"name": f"E{i}", "provider": "gemini", "model": "m"}) for i in range(12)]
        jobs += [lambda: ex.duplicate_extractor(first.id) for _ in range(4)]
        jobs += [lambda: ex.set_key_saved(first.id, True)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            for future in [pool.submit(job) for job in jobs]:
                future.result()

        names = {item.name for item in ex.load_extractors()}
        self.assertEqual(len(names), 1 + 12 + 4)
        self.assertTrue({f"E{i}" for i in range(12)} <= names)
        self.assertTrue(ex.get_extractor(first.id).key_saved)
        self.assertEqual(list(self.config_dir.glob("*.tmp")), [])

    def test_delete_removes_the_definition(self):
        first = ex.create_extractor({"name": "A", "provider": "gemini", "model": "m"})
        second = ex.create_extractor({"name": "B", "provider": "gemini", "model": "m"})
        self.assertEqual(ex.delete_extractor(first.id), first)
        self.assertEqual(ex.load_extractors(), [second])
        self.assertIsNone(ex.get_extractor(first.id))
        with self.assertRaisesRegex(ex.ExtractorError, "no longer exists"):
            ex.delete_extractor(first.id)

    def test_summary_describes_each_providers_settings(self):
        local = ex.create_extractor({"name": "L", "provider": "openai_compatible", "model": "q", "base_url": "http://h/v1", "context_length": "8000"})
        router = ex.create_extractor({"name": "R", "provider": "openrouter", "model": "v/m", "openrouter_allow_fallbacks": "deny"})
        gemini = ex.create_extractor({"name": "G", "provider": "gemini", "model": "m"})
        self.assertIn(("Context length", "8,000 tokens"), local.summary())
        self.assertIn(("Fallbacks", "Not allowed"), router.summary())
        self.assertIn(("Route to provider", "Automatic"), router.summary())
        self.assertIn(("Service tier", "Flex (lower cost, variable latency)"), gemini.summary())
        self.assertNotIn("Context length", dict(ex.create_extractor({"name": "L2", "provider": "openai_compatible", "model": "q", "base_url": "http://h/v1"}).summary()))
        # An empty maximum output names the limit each provider then uses.
        self.assertIn(("Maximum output", "Server default"), local.summary())
        self.assertIn(("Maximum output", "Endpoint maximum"), router.summary())
        self.assertIn(("Maximum output", "Model maximum"), gemini.summary())
        capped = ex.create_extractor({"name": "C", "provider": "openrouter", "model": "v/m", "max_output_tokens": "65536"})
        self.assertIn(("Maximum output", "65,536 tokens"), capped.summary())

    def test_unreadable_or_damaged_files_are_tolerated(self):
        ex.create_extractor({"name": "Good", "provider": "gemini", "model": "m"})
        path = self.config_dir / "extractors.json"
        stored = self.stored()
        stored["extractors"] += [{"name": "No id"}, "junk", {"id": "x", "name": "Bad", "provider": "unknown"}]
        path.write_text(json.dumps(stored), encoding="utf-8")
        self.assertEqual([item.name for item in ex.load_extractors()], ["Good"])
        # A record this version can't load survives the next save untouched.
        ex.create_extractor({"name": "Another", "provider": "gemini", "model": "m"})
        self.assertIn({"id": "x", "name": "Bad", "provider": "unknown"}, self.stored()["extractors"])
        self.assertEqual([item.name for item in ex.load_extractors()], ["Good", "Another"])

    def test_an_unreadable_extractors_file_is_never_overwritten(self):
        ex.create_extractor({"name": "Good", "provider": "gemini", "model": "m"})
        path = self.config_dir / "extractors.json"
        for contents in ["{broken", "[]", '{"extractors": "nope"}']:
            with self.subTest(contents=contents):
                path.write_text(contents, encoding="utf-8")
                with self.assertRaises(ex.ExtractorsUnreadable):
                    ex.load_extractors()
                with self.assertRaises(ex.ExtractorsUnreadable):
                    ex.create_extractor({"name": "New", "provider": "gemini", "model": "m"})
                with self.assertRaises(ex.ExtractorsUnreadable):
                    ex.save_extractors([])
                self.assertEqual(path.read_text(encoding="utf-8"), contents)

    def test_saving_works_where_permissions_cannot_be_set(self):
        with patch.object(Path, "chmod", side_effect=OSError("unsupported")):
            created = ex.create_extractor({"name": "Lab", "provider": "gemini", "model": "m"})
        self.assertEqual(ex.load_extractors(), [created])

    def test_the_provider_check_runs_without_blocking_other_changes(self):
        import threading

        target = ex.create_extractor({"name": "Target", "provider": "gemini", "model": "m"})
        finished = []

        def check(current, updated=None):
            # Another request changes extractors while the check is in flight.
            other = threading.Thread(target=lambda: finished.append(
                ex.create_extractor({"name": f"Other {len(finished)}", "provider": "gemini", "model": "m"})
            ))
            other.start()
            other.join(timeout=5)
            self.assertFalse(other.is_alive(), "the check held the extractors lock")
            ex.set_key_saved(target.id, True)

        ex.create_extractor({"name": "Checked", "provider": "gemini", "model": "m"}, check=check)
        updated = ex.update_extractor(target.id, {"name": "Target", "model": "m2"}, check=check)

        self.assertEqual(len(finished), 2)
        # The key status recorded during the check is kept, not overwritten.
        self.assertTrue(updated.key_saved)
        self.assertTrue(ex.get_extractor(target.id).key_saved)
        names = [item.name for item in ex.load_extractors()]
        self.assertEqual(sorted(names), ["Checked", "Other 0", "Other 1", "Target"])

    def test_find_by_name_ignores_case(self):
        created = ex.create_extractor({"name": "Lab Gemini", "provider": "gemini", "model": "m"})
        self.assertEqual(ex.find_extractor_by_name(" lab gemini "), created)
        self.assertIsNone(ex.find_extractor_by_name("other"))


class ProjectExtractorTests(ExtractorTestCase):
    def setUp(self):
        super().setUp()
        self.project = Path(self._tmp.name) / "project"
        self.project.mkdir()

    def test_old_projects_preselect_the_extractor_matching_their_last_run(self):
        from auto_zcurve.config import load_selected_extractor_id

        self.assertEqual(ex.project_extractor(self.project), ex.ProjectExtractor(None))
        ex.create_extractor({"name": "Other", "provider": "gemini", "model": "gemini-y"})
        match = ex.create_extractor({"name": "Match", "provider": "gemini", "model": "gemini-x"})
        legacy = self.project / ".auto_zcurve" / "run_settings.json"
        legacy.parent.mkdir()
        legacy.write_text(json.dumps({"provider": "openrouter", "primary_model": "gemini-x"}), encoding="utf-8")
        self.assertEqual(ex.project_extractor(self.project), ex.ProjectExtractor(None))
        legacy.write_text(json.dumps({"provider": "gemini", "primary_model": "gemini-x"}), encoding="utf-8")

        self.assertEqual(ex.project_extractor(self.project), ex.ProjectExtractor(match))
        self.assertEqual(load_selected_extractor_id(self.project), match.id)

    def test_last_run_extractor_is_used_and_deleted_ones_are_flagged(self):
        from auto_zcurve.config import save_run_settings

        router = ex.create_extractor({"name": "R", "provider": "openrouter", "model": "v/m"})
        save_run_settings(self.project, ex.run_settings_for(router, ex.load_app_settings()))
        self.assertEqual(ex.project_extractor(self.project), ex.ProjectExtractor(router))
        ex.delete_extractor(router.id)
        self.assertEqual(ex.project_extractor(self.project), ex.ProjectExtractor(None, deleted=True))

    def test_unreadable_run_settings_mean_no_choice(self):
        path = self.project / ".auto_zcurve" / "run_settings.json"
        path.parent.mkdir()
        path.write_text("{broken", encoding="utf-8")
        self.assertEqual(ex.project_extractor(self.project), ex.ProjectExtractor(None))



class ExtractorMigrationTests(ExtractorTestCase):
    def write_legacy(self, settings: dict, preferences: dict | None = None) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        if preferences is not None:
            (self.config_dir / "preferences.json").write_text(json.dumps(preferences), encoding="utf-8")

    def test_old_provider_setup_becomes_extractors_once(self):
        self.write_legacy(
            {
                "pdf_parser": "native",
                "max_upload_size_mb": 64,
                "reasoning_effort": "low",
                "service_tier": "priority",
                "request_timeout_sec": 300,
                "parallel_requests": 2,
                "request_delay_sec": 10,
                "default_gemini_model": "gemini-3.1-pro-preview",
                "default_openrouter_model": "",
                "openai_base_url": "http://localhost:8000/v1",
                "openai_response_format": "json_object",
            },
            {"saved_api_key_configured": True, "openrouter_saved_api_key_configured": True},
        )

        migrated = ex.load_extractors()

        by_name = {item.name: item for item in migrated}
        self.assertEqual(list(by_name), ["Gemini", "OpenRouter", "OpenAI-compatible"])
        gemini = by_name["Gemini"]
        self.assertEqual((gemini.model, gemini.service_tier), ("gemini-3.1-pro-preview", "priority"))
        self.assertEqual((gemini.request_timeout_sec, gemini.parallel_requests, gemini.request_delay_sec), (300, 2, 10))
        self.assertTrue(gemini.key_saved)
        router = by_name["OpenRouter"]
        self.assertEqual((router.model, router.reasoning_effort, router.key_saved), ("", "low", True))
        local = by_name["OpenAI-compatible"]
        self.assertEqual((local.base_url, local.response_format, local.key_saved), ("http://localhost:8000/v1", "json_object", False))
        self.assertEqual(ex.legacy_claims(), {gemini.id: "gemini_api_key", router.id: "openrouter_api_key"})

        self.assertFalse((self.config_dir / "preferences.json").exists())
        remaining = json.loads((self.config_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(remaining, {"pdf_parser": "native", "max_upload_size_mb": 64, "retry_extractor_change": "rerun_all"})
        self.assertEqual(ex.load_extractors(), migrated)

    def test_moved_keys_clear_the_migration_markers(self):
        self.write_legacy(
            {"default_openrouter_model": "vendor/model"},
            {"saved_api_key_configured": True, "openrouter_saved_api_key_configured": True},
        )
        gemini, router = ex.load_extractors()
        self.assertEqual(gemini.model, "gemini-3.6-flash")

        ex.record_legacy_adoption({gemini.id: True})
        self.assertEqual(ex.legacy_claims(), {router.id: "openrouter_api_key"})
        ex.record_legacy_adoption({router.id: False})

        self.assertEqual(ex.legacy_claims(), {})
        self.assertTrue(ex.get_extractor(gemini.id).key_saved)
        self.assertFalse(ex.get_extractor(router.id).key_saved)

    def test_unusable_old_settings_are_skipped(self):
        self.write_legacy({"default_gemini_model": "gemini-x", "openai_base_url": "ftp://not-allowed"})
        self.assertEqual([item.name for item in ex.load_extractors()], ["Gemini"])

    def test_default_only_gemini_settings_create_no_extractor(self):
        self.write_legacy({"default_gemini_model": "gemini-3.6-flash", "service_tier": "flex", "parallel_requests": 4})
        self.assertEqual(ex.load_extractors(), [])

    def test_a_non_default_gemini_setting_creates_an_extractor(self):
        self.write_legacy({"default_gemini_model": "gemini-3.6-flash", "service_tier": "priority"})
        (gemini,) = ex.load_extractors()
        self.assertEqual((gemini.model, gemini.service_tier, gemini.key_saved), ("gemini-3.6-flash", "priority", False))

    def test_an_endpoint_key_without_a_base_url_keeps_an_extractor_that_asks_for_one(self):
        from auto_zcurve.config import AppSettings

        self.write_legacy({"openai_response_format": "none"}, {"openai_compatible_saved_api_key_configured": True})
        with patch("auto_zcurve.credentials._keyring_module", side_effect=AssertionError("keychain read")):
            (local,) = ex.load_extractors()
        self.assertEqual((local.provider, local.base_url, local.response_format), ("openai_compatible", "", "none"))
        self.assertTrue(local.key_saved)
        self.assertIn(("API base URL", "Not set"), local.summary())
        self.assertEqual(ex.legacy_claims(), {local.id: "openai_compatible_api_key"})
        with self.assertRaisesRegex(ex.ExtractorError, "API base URL"):
            ex.run_settings_for(replace(local, model="q"), AppSettings())
        with self.assertRaisesRegex(ex.ExtractorError, "API base URL"):
            ex.update_extractor(local.id, {**local.snapshot(), "model": "q"})
        fixed = ex.update_extractor(local.id, {**local.snapshot(), "model": "q", "base_url": "http://h/v1"})
        self.assertEqual(ex.run_settings_for(fixed, AppSettings()).base_url, "http://h/v1")
        self.assertEqual(ex.legacy_claims(), {local.id: "openai_compatible_api_key"})

    def test_a_saved_key_survives_unusable_old_settings(self):
        self.write_legacy({"openai_base_url": "ftp://not-allowed"}, {"openai_compatible_saved_api_key_configured": True})
        (local,) = ex.load_extractors()
        self.assertEqual((local.base_url, local.response_format), ("", "json_schema"))
        self.assertEqual(ex.legacy_claims(), {local.id: "openai_compatible_api_key"})

    def test_old_files_that_cannot_be_read_are_left_in_place(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        settings = self.config_dir / "settings.json"
        preferences = self.config_dir / "preferences.json"
        settings.write_text("{broken settings", encoding="utf-8")
        preferences.write_text("{broken flags", encoding="utf-8")

        self.assertEqual(ex.load_extractors(), [])

        self.assertEqual(settings.read_text(encoding="utf-8"), "{broken settings")
        self.assertEqual(preferences.read_text(encoding="utf-8"), "{broken flags")

    def test_migration_is_safe_to_rerun(self):
        preferences = {"openai_compatible_saved_api_key_configured": True}
        self.write_legacy({"default_gemini_model": "gemini-3.6-flash"}, preferences)
        migrated = ex.load_extractors()
        # An interrupted cleanup leaves old files behind; they are not migrated again.
        self.write_legacy({"default_gemini_model": "gemini-x"}, preferences)
        self.assertEqual(ex.load_extractors(), migrated)
        self.assertEqual([item.provider for item in migrated], ["openai_compatible"])

    def test_fresh_install_has_no_extractors(self):
        self.assertEqual(ex.load_extractors(), [])
        self.assertEqual(ex.legacy_claims(), {})


class ExtractorRunTests(ExtractorTestCase):
    def test_run_settings_carry_the_extractor_and_a_snapshot(self):
        from auto_zcurve.config import AppSettings

        local = ex.create_extractor({
            "name": "Local", "provider": "openai_compatible", "model": "qwen",
            "base_url": "http://localhost:8000/v1/", "response_format": "none",
            "context_length": "32000", "parallel_requests": "4", "max_output_tokens": "16000",
        })

        settings = ex.run_settings_for(local, AppSettings(pdf_parser="native", max_upload_size_mb=12))
        self.assertEqual((settings.max_output_tokens, settings.extractor["max_output_tokens"]), (16000, 16000))

        self.assertEqual((settings.provider, settings.primary_model), ("openai_compatible", "qwen"))
        self.assertEqual((settings.base_url, settings.response_format, settings.context_length), ("http://localhost:8000/v1", "none", 32000))
        self.assertEqual((settings.parallel_requests, settings.max_upload_size_mb), (4, 12))
        self.assertEqual(settings.extractor_id, local.id)
        self.assertEqual(settings.extractor["name"], "Local")
        self.assertNotIn("key_saved", settings.extractor)

    def test_an_extractor_without_a_model_cannot_run(self):
        from auto_zcurve.config import AppSettings

        router = ex._build({"name": "Old", "provider": "openrouter", "model": ""}, extractor_id="x", require_model=False)
        with self.assertRaisesRegex(ex.ExtractorError, "Choose a model"):
            ex.run_settings_for(router, AppSettings())

    def test_model_check_asks_the_provider(self):
        from auto_zcurve.models import ModelOption

        gemini = ex.create_extractor({"name": "G", "provider": "gemini", "model": "gemini-x"})
        listed = [ModelOption("gemini-x", "Gemini X")]
        with patch.object(ex, "list_live_models", return_value=listed) as live:
            ex.check_model(gemini, "key")
        live.assert_called_once_with("key", "gemini")
        with patch.object(ex, "list_live_models", return_value=[]), self.assertRaisesRegex(ex.ExtractorError, "isn't available"):
            ex.check_model(gemini, "key")

        router = ex.create_extractor({"name": "R", "provider": "openrouter", "model": "vendor/model"})
        with patch.object(ex, "validate_model_option", side_effect=ValueError("no structured outputs")):
            with self.assertRaisesRegex(ex.ExtractorError, "no structured outputs"):
                ex.check_model(router, "key")

        local = ex.create_extractor({"name": "L", "provider": "openai_compatible", "model": "m", "base_url": "http://h/v1"})
        with patch.object(ex, "list_live_models") as live, patch.object(ex, "validate_model_option") as validate:
            ex.check_model(local, None)
        live.assert_not_called()
        validate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
