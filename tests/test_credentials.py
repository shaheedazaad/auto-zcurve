import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auto_zcurve.credentials import (
    credentials_path,
    delete_saved_api_key,
    load_saved_api_key,
    save_api_key,
)
from auto_zcurve.env import resolve_api_key


class CredentialTests(unittest.TestCase):
    def test_save_load_delete_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}, clear=True):
                    path = save_api_key("abc123")

                    self.assertEqual(path, credentials_path())
                    self.assertEqual(load_saved_api_key(), "abc123")
                    self.assertEqual(resolve_api_key(project_dir=Path(tmp), explicit_key=None), "abc123")
                    self.assertTrue(delete_saved_api_key())
                    self.assertIsNone(load_saved_api_key())
            finally:
                os.chdir(cwd)

    def test_empty_key_is_not_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}, clear=False):
                with self.assertRaises(ValueError):
                    save_api_key(" ")
                self.assertFalse(Path(tmp, "auto-zcurve", "credentials.json").exists())


if __name__ == "__main__":
    unittest.main()
