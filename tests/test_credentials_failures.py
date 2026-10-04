from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_zcurve import credentials as c
from auto_zcurve.providers import PROVIDER_REGISTRY


class CredentialBackendTests(unittest.TestCase):
    def test_preference_directory_precedence(self):
        for environment, expected in [({'XDG_CONFIG_HOME': '/xdg', 'APPDATA': '/app'}, '/xdg/auto-zcurve'), ({'APPDATA': '/app'}, '/app/auto-zcurve'), ({}, '/home/test/.config/auto-zcurve')]:
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), patch.object(Path, 'home', return_value=Path('/home/test')):
                self.assertEqual(c.credentials_dir(), Path(expected))

    def test_backend_detection_never_reads_passwords(self):
        for priority, available in [(1, True), ('2', True), (0, False), (-1, False), ('invalid', False)]:
            keyring = Mock()
            keyring.get_keyring.return_value = SimpleNamespace(priority=priority)
            with self.subTest(priority=priority), patch.dict('sys.modules', {'keyring': keyring}):
                self.assertEqual(c.credential_store_available(), available)
                keyring.get_password.assert_not_called()
        with patch.dict('sys.modules', {'keyring': None}):
            self.assertFalse(c.credential_store_available())
        keyring = Mock()
        keyring.get_keyring.side_effect = RuntimeError('backend failed')
        with patch.dict('sys.modules', {'keyring': keyring}), self.assertRaisesRegex(c.CredentialStoreUnavailable, 'could not be opened'):
            c._keyring_module()

    def test_loading_failure_or_whitespace_is_absent(self):
        for value in [None, '', '  ', ' key ']:
            keyring = Mock()
            keyring.get_password.return_value = value
            with patch.object(c, '_keyring_module', return_value=keyring):
                self.assertEqual(c.load_saved_api_key(), 'key' if value == ' key ' else None)
        keyring.get_password.side_effect = RuntimeError('locked')
        with patch.object(c, '_keyring_module', return_value=keyring):
            self.assertIsNone(c.load_saved_api_key())

    def test_save_failure_does_not_mark_key_configured(self):
        keyring = Mock()
        keyring.set_password.side_effect = RuntimeError('locked')
        with patch.object(c, '_keyring_module', return_value=keyring), patch.object(c, '_set_saved_api_key_configured') as mark, self.assertRaisesRegex(c.CredentialStoreUnavailable, 'rejected the key'):
            c.save_api_key('secret')
        mark.assert_not_called()

    def test_provider_keys_are_separate_and_delete_failure_clears_intent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'credentials_path', return_value=Path(tmp) / 'prefs.json'), patch.object(c, '_keyring_module') as backend:
            for provider, definition in PROVIDER_REGISTRY.items():
                self.assertEqual(c.save_api_key(' secret ', provider), 'operating-system credential store')
                backend.return_value.set_password.assert_called_with(c.SERVICE_NAME, definition.credential_key, 'secret')
                self.assertTrue(c.saved_api_key_configured(provider))
            preferences = json.loads(c.credentials_path().read_text())
            self.assertEqual(set(preferences), set(c.SAVED_API_KEY_KEYS.values()))
            self.assertNotIn('secret', c.credentials_path().read_text())
            backend.return_value.delete_password.side_effect = RuntimeError('missing')
            for provider, definition in PROVIDER_REGISTRY.items():
                self.assertFalse(c.delete_saved_api_key(provider))
                self.assertFalse(c.saved_api_key_configured(provider))
                backend.return_value.delete_password.assert_called_with(c.SERVICE_NAME, definition.credential_key)
            self.assertEqual(json.loads(c.credentials_path().read_text()), {})


class PreferenceTests(unittest.TestCase):
    def test_invalid_and_unreadable_preferences_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'prefs.json'
            with patch.object(c, 'credentials_path', return_value=path):
                self.assertEqual(c._load_preferences(), {})
                for contents in ['broken', '[]', 'null', '{"other": 3}']:
                    path.write_text(contents)
                    self.assertEqual(c._load_preferences(), {'other': 3} if contents.startswith('{') else {})
                with patch.object(Path, 'open', side_effect=OSError('denied')):
                    self.assertEqual(c._load_preferences(), {})

    def test_saving_preserves_preferences_even_if_chmod_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'credentials_path', return_value=Path(tmp) / 'nested/prefs.json'), patch.object(Path, 'chmod', side_effect=OSError('unsupported')):
            c._save_preferences({'other': 3})
            c.save_last_project_dir(Path(tmp))
            self.assertEqual(c.load_last_project_dir(), Path(tmp).resolve())
            self.assertEqual(c._load_preferences()['other'], 3)
            self.assertTrue(c.credentials_path().read_text().endswith('\n'))

    def test_last_project_must_be_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'file'
            file.touch()
            for value, expected in [(None, None), ('', None), (str(file), None), (str(file / 'missing'), None), (tmp, Path(tmp))]:
                with patch.object(c, '_load_preferences', return_value={c.LAST_PROJECT_KEY: value}):
                    self.assertEqual(c.load_last_project_dir(), expected)
