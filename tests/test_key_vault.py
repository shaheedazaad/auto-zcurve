from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from auto_zcurve import credentials as c
from auto_zcurve.credentials import KeyVault, VaultUnavailable


class FakeKeyring:
    """An in-memory keyring that counts every call, like a Keychain prompt would."""

    def __init__(self, items: dict[tuple[str, str], str] | None = None) -> None:
        self.items = dict(items or {})
        self.reads = 0
        self.writes = 0
        self.deletes = 0

    def get_password(self, service, username):
        self.reads += 1
        return self.items.get((service, username))

    def set_password(self, service, username, value):
        self.writes += 1
        self.items[(service, username)] = value

    def delete_password(self, service, username):
        self.deletes += 1
        self.items.pop((service, username))

    def vault(self) -> dict:
        return json.loads(self.items[("auto-zcurve", "extractor_keys")])["keys"]


def with_keyring(keyring):
    return patch.object(c, "_keyring_module", return_value=keyring)


class KeyVaultTests(unittest.TestCase):
    def test_many_extractor_keys_cost_one_key_vault_read(self):
        keyring = FakeKeyring({("auto-zcurve", "extractor_keys"): json.dumps(
            {"version": 1, "keys": {"a": "key-a", "b": "key-b"}}
        )})
        vault = KeyVault()
        with with_keyring(keyring):
            self.assertEqual(vault.get("a"), "key-a")
            self.assertEqual(vault.get("b"), "key-b")
            self.assertIsNone(vault.get("missing"))
        self.assertEqual(keyring.reads, 1)
        self.assertEqual(keyring.writes, 0)

    def test_saving_one_key_writes_once_and_keeps_the_others(self):
        keyring = FakeKeyring({("auto-zcurve", "extractor_keys"): json.dumps(
            {"version": 1, "keys": {"a": "key-a"}}
        )})
        vault = KeyVault()
        with with_keyring(keyring):
            self.assertTrue(vault.set("b", "  key-b  "))
            self.assertEqual(vault.get("b"), "key-b")
        self.assertEqual(keyring.vault(), {"a": "key-a", "b": "key-b"})
        self.assertEqual((keyring.reads, keyring.writes), (1, 1))

    def test_removing_one_key_leaves_the_others(self):
        keyring = FakeKeyring({("auto-zcurve", "extractor_keys"): json.dumps(
            {"version": 1, "keys": {"a": "key-a", "b": "key-b"}}
        )})
        vault = KeyVault()
        with with_keyring(keyring):
            vault.delete("a")
            vault.delete("never-saved")
            self.assertIsNone(vault.get("a"))
        self.assertEqual(keyring.vault(), {"b": "key-b"})
        self.assertEqual(keyring.writes, 1)

    def test_an_unreadable_vault_is_reported_and_never_overwritten(self):
        class DenyingKeyring(FakeKeyring):
            def get_password(self, service, username):
                raise RuntimeError("User canceled the operation.")

        corrupt = FakeKeyring({("auto-zcurve", "extractor_keys"): "{not json"})
        for keyring in (DenyingKeyring({("auto-zcurve", "extractor_keys"): "{}"}), corrupt):
            with self.subTest(keyring=type(keyring).__name__), with_keyring(keyring):
                vault = KeyVault()
                with self.assertRaisesRegex(VaultUnavailable, "Couldn't unlock saved keys"):
                    vault.get("a")
                with self.assertRaises(VaultUnavailable):
                    vault.set("a", "new")
                with self.assertRaises(VaultUnavailable):
                    vault.delete("a")
                self.assertEqual(keyring.writes, 0)

    def test_without_a_credential_store_keys_last_for_the_session_only(self):
        unavailable = c.CredentialStoreUnavailable("no keyring")
        with patch.object(c, "_keyring_module", side_effect=unavailable):
            vault = KeyVault()
            self.assertFalse(vault.persistent)
            self.assertFalse(vault.set("a", "key-a"))
            self.assertEqual(vault.get("a"), "key-a")
            vault.delete("a")
            self.assertIsNone(vault.get("a"))

    def test_first_open_moves_old_provider_keys_into_the_vault(self):
        keyring = FakeKeyring({
            ("auto-zcurve", "gemini_api_key"): "old-gemini",
            ("auto-zcurve", "openrouter_api_key"): "",
        })
        adopted = []
        vault = KeyVault(
            legacy_claims=lambda: {"g": "gemini_api_key", "o": "openrouter_api_key"},
            on_legacy_adopted=adopted.append,
        )
        with with_keyring(keyring):
            self.assertEqual(vault.get("g"), "old-gemini")
            self.assertIsNone(vault.get("o"))
        self.assertEqual(keyring.vault(), {"g": "old-gemini"})
        self.assertNotIn(("auto-zcurve", "gemini_api_key"), keyring.items)
        self.assertEqual(adopted, [{"g": True, "o": False}])

    def test_old_keys_survive_when_the_vault_cannot_be_written(self):
        class ReadOnlyKeyring(FakeKeyring):
            def set_password(self, service, username, value):
                raise RuntimeError("write denied")

        keyring = ReadOnlyKeyring({("auto-zcurve", "gemini_api_key"): "old-gemini"})
        adopted = []
        vault = KeyVault(legacy_claims=lambda: {"g": "gemini_api_key"}, on_legacy_adopted=adopted.append)
        with with_keyring(keyring):
            self.assertEqual(vault.get("g"), "old-gemini")
        self.assertEqual(keyring.items, {("auto-zcurve", "gemini_api_key"): "old-gemini"})
        self.assertEqual(adopted, [])

    def test_a_vault_without_a_key_map_is_unreadable(self):
        keyring = FakeKeyring({("auto-zcurve", "extractor_keys"): json.dumps({"version": 1})})
        with with_keyring(keyring), self.assertRaises(VaultUnavailable):
            KeyVault().get("a")

    def test_an_unreadable_old_key_leaves_every_old_item_for_next_session(self):
        class LockedKeyring(FakeKeyring):
            def get_password(self, service, username):
                if username == "openrouter_api_key":
                    raise RuntimeError("locked")
                return super().get_password(service, username)

        keyring = LockedKeyring({
            ("auto-zcurve", "gemini_api_key"): "old-gemini",
            ("auto-zcurve", "openrouter_api_key"): "old-openrouter",
        })
        adopted = []
        vault = KeyVault(
            legacy_claims=lambda: {"g": "gemini_api_key", "o": "openrouter_api_key"},
            on_legacy_adopted=adopted.append,
        )
        with with_keyring(keyring):
            vault.get("o")
        self.assertEqual((keyring.writes, keyring.deletes), (0, 0))
        self.assertIn(("auto-zcurve", "openrouter_api_key"), keyring.items)
        self.assertEqual(adopted, [])

    def test_removing_a_key_also_removes_its_unmoved_old_item(self):
        class LockedKeyring(FakeKeyring):
            def get_password(self, service, username):
                if username == "openrouter_api_key":
                    raise RuntimeError("locked")
                return super().get_password(service, username)

        keyring = LockedKeyring({("auto-zcurve", "openrouter_api_key"): "old-openrouter"})
        vault = KeyVault(legacy_claims=lambda: {"o": "openrouter_api_key"})
        with with_keyring(keyring):
            self.assertIsNone(vault.get("o"))
            vault.delete("o", "openrouter_api_key")
            vault.delete("o", "gemini_api_key")  # Never existed: tolerated.
        self.assertNotIn(("auto-zcurve", "openrouter_api_key"), keyring.items)

    def test_an_old_item_that_cannot_be_removed_is_reported(self):
        class StuckKeyring(FakeKeyring):
            def delete_password(self, service, username):
                raise RuntimeError("cannot delete")

        class Unverifiable(StuckKeyring):
            def get_password(self, service, username):
                if username == "openrouter_api_key":
                    raise RuntimeError("locked")
                return super().get_password(service, username)

        for keyring in (StuckKeyring({("auto-zcurve", "openrouter_api_key"): "old"}), Unverifiable()):
            with self.subTest(keyring=type(keyring).__name__), with_keyring(keyring):
                with self.assertRaises(VaultUnavailable):
                    KeyVault().delete("o", "openrouter_api_key")

    def test_a_claim_stays_pending_until_its_old_item_is_removed(self):
        class StuckKeyring(FakeKeyring):
            def delete_password(self, service, username):
                if username == "openrouter_api_key":
                    raise RuntimeError("cannot delete")
                return super().delete_password(service, username)

        keyring = StuckKeyring({
            ("auto-zcurve", "gemini_api_key"): "old-gemini",
            ("auto-zcurve", "openrouter_api_key"): "old-openrouter",
        })
        adopted = []
        vault = KeyVault(
            legacy_claims=lambda: {"g": "gemini_api_key", "o": "openrouter_api_key"},
            on_legacy_adopted=adopted.append,
        )
        with with_keyring(keyring):
            self.assertEqual(vault.get("o"), "old-openrouter")
        # Only the claim whose item is gone is cleared; the other is retried later.
        self.assertEqual(adopted, [{"g": True}])
        self.assertEqual(keyring.vault(), {"g": "old-gemini", "o": "old-openrouter"})

    def test_concurrent_saves_keep_every_key(self):
        import threading
        import time

        class SlowKeyring(FakeKeyring):
            def set_password(self, service, username, value):
                time.sleep(0.01)  # Widens the window between reading and writing the vault.
                super().set_password(service, username, value)

        keyring = SlowKeyring()
        vault = KeyVault()
        with with_keyring(keyring):
            vault.get("warm")  # Load once, as the app does before concurrent saves.
            threads = [threading.Thread(target=vault.set, args=(f"x{index}", f"key-{index}")) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(keyring.vault(), {f"x{index}": f"key-{index}" for index in range(8)})

    def test_moving_old_keys_tolerates_items_that_cannot_be_removed(self):
        class StubbornKeyring(FakeKeyring):
            def delete_password(self, service, username):
                raise RuntimeError("cannot delete")

        keyring = StubbornKeyring({
            ("auto-zcurve", "extractor_keys"): json.dumps({"keys": {"g": "already-moved"}}),
            ("auto-zcurve", "gemini_api_key"): "stale-copy",
        })
        vault = KeyVault(legacy_claims=lambda: {"g": "gemini_api_key", "o": "openrouter_api_key"})
        with with_keyring(keyring):
            self.assertEqual(vault.get("g"), "already-moved")
            self.assertIsNone(vault.get("o"))
        self.assertEqual(keyring.vault(), {"g": "already-moved"})

    def test_a_removal_that_cannot_be_saved_is_reported_and_undone(self):
        class ReadOnlyKeyring(FakeKeyring):
            def set_password(self, service, username, value):
                raise RuntimeError("write denied")

        keyring = ReadOnlyKeyring({("auto-zcurve", "extractor_keys"): json.dumps({"keys": {"a": "key-a"}})})
        vault = KeyVault()
        with with_keyring(keyring):
            with self.assertRaises(VaultUnavailable):
                vault.delete("a")
            self.assertEqual(vault.get("a"), "key-a")

    def test_an_empty_key_is_rejected(self):
        with with_keyring(FakeKeyring()), self.assertRaises(ValueError):
            KeyVault().set("a", "   ")


if __name__ == "__main__":
    unittest.main()
