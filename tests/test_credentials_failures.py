from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_zcurve import credentials as c


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

    def test_legacy_preferences_live_next_to_the_settings(self):
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': '/xdg'}, clear=True):
            self.assertEqual(c.legacy_preferences_path(), Path('/xdg/auto-zcurve/preferences.json'))


if __name__ == "__main__":
    unittest.main()
