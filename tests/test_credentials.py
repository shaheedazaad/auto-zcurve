import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auto_zcurve.credentials import (
    CredentialStoreUnavailable,
    credentials_path,
    delete_saved_api_key,
    load_saved_api_key,
    saved_api_key_configured,
    save_api_key,
)
from auto_zcurve.env import resolve_api_key


class CredentialTests(unittest.TestCase):
    def test_save_load_delete_api_key(self):
        class FakeKeyring:
            value = None

            @classmethod
            def set_password(cls, service, key, value):
                cls.value = value

            @classmethod
            def get_password(cls, service, key):
                return cls.value

            @classmethod
            def delete_password(cls, service, key):
                cls.value = None

        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}, clear=True):
                    with patch("auto_zcurve.credentials._keyring_module", return_value=FakeKeyring):
                        location = save_api_key("abc123")

                        self.assertEqual(location, "operating-system credential store")
                        self.assertTrue(saved_api_key_configured())
                        self.assertEqual(load_saved_api_key(), "abc123")
                        self.assertEqual(resolve_api_key(project_dir=Path(tmp), explicit_key=None), "abc123")
                        self.assertTrue(delete_saved_api_key())
                        self.assertFalse(saved_api_key_configured())
                        self.assertIsNone(load_saved_api_key())
                    self.assertNotIn("abc123", credentials_path().read_text(encoding="utf-8"))
            finally:
                os.chdir(cwd)

    def test_empty_key_is_not_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}, clear=False):
                with self.assertRaises(ValueError):
                    save_api_key(" ")
                self.assertFalse(Path(tmp, "auto-zcurve", "credentials.json").exists())

    def test_unavailable_keyring_never_falls_back_to_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}, clear=False):
                with patch(
                    "auto_zcurve.credentials._keyring_module",
                    side_effect=CredentialStoreUnavailable("no keyring"),
                ):
                    with self.assertRaises(CredentialStoreUnavailable):
                        save_api_key("secret")
                    self.assertIsNone(load_saved_api_key())
                self.assertFalse(credentials_path().exists())


if __name__ == "__main__":
    unittest.main()
