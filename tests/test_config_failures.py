import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_zcurve import config as c


class ConfigFailureTests(unittest.TestCase):
    def test_invalid_enum_values_and_model_defaults(self):
        for normalize in [c.normalize_pdf_parser, c.normalize_reasoning_effort, c.normalize_service_tier, c.normalize_response_format]:
            with self.subTest(normalize=normalize.__name__), self.assertRaises(ValueError):
                normalize('unknown')
        self.assertEqual(c.normalize_default_gemini_model('models/'), c.DEFAULT_MODEL)
        self.assertEqual(c.normalize_default_gemini_model(None), c.DEFAULT_MODEL)
        self.assertEqual(c.normalize_default_openrouter_model(' '), '')

    def test_invalid_app_settings_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'app_settings_path', return_value=Path(tmp) / 'settings.json'):
            path = c.app_settings_path()
            for contents in ['[]', '{broken', '{"parallel_requests":"bad"}', '{"openai_base_url":"file:///tmp"}', '{"openai_response_format":"bad"}']:
                path.write_text(contents)
                self.assertEqual(c.load_app_settings(), c.AppSettings())
            with patch.object(Path, 'read_text', side_effect=OSError('denied')):
                self.assertEqual(c.load_app_settings(), c.AppSettings())

    def test_app_settings_clamp_and_atomic_failure_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'app_settings_path', return_value=Path(tmp) / 'settings.json'):
            with patch.object(Path, 'chmod', side_effect=OSError('unsupported')):
                c.save_app_settings(c.AppSettings(request_timeout_sec=1, parallel_requests=99, request_delay_sec=-1, max_upload_size_mb=999))
            settings = c.load_app_settings()
            self.assertEqual((settings.request_timeout_sec, settings.parallel_requests, settings.request_delay_sec, settings.max_upload_size_mb), (30, 32, 0, 512))
            path = c.app_settings_path()
            original = path.read_bytes()
            with patch.object(Path, 'replace', side_effect=OSError('denied')), self.assertRaises(OSError):
                c.save_app_settings(c.AppSettings())
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(path.with_suffix('.json.tmp').exists())

    def test_legacy_migration_handles_missing_invalid_and_preserves_other_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self.assertIsNone(c.legacy_effect_definition(project))
            c.remove_legacy_effect_definition(project)
            path = c.settings_path(project)
            path.parent.mkdir()
            for contents in ['[]', 'broken', '{}', '{"effect_definition": 4}', '{"effect_definition": " "}']:
                path.write_text(contents)
                self.assertIsNone(c.legacy_effect_definition(project))
                c.remove_legacy_effect_definition(project)
            path.write_text('{"effect_definition": " target ", "other": 5}')
            self.assertEqual(c.legacy_effect_definition(project), 'target')
            c.remove_legacy_effect_definition(project)
            self.assertEqual(json.loads(path.read_text()), {'other': 5})
            self.assertFalse(path.with_suffix('.json.tmp').exists())
            path.write_text('{"effect_definition": "target"}')
            with patch.object(Path, 'replace', side_effect=OSError('denied')):
                c.remove_legacy_effect_definition(project)
            self.assertEqual(c.legacy_effect_definition(project), 'target')
            self.assertFalse(path.with_suffix('.json.tmp').exists())
            with patch.object(Path, 'open', side_effect=OSError('denied')):
                self.assertIsNone(c.legacy_effect_definition(project))
                c.remove_legacy_effect_definition(project)

    def test_run_settings_without_model_are_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            path = c.settings_path(project)
            path.parent.mkdir()
            path.write_text('{}')
            self.assertIsNone(c.load_run_settings(project))
