from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable


SERVICE_NAME = "auto-zcurve"
VAULT_USERNAME = "extractor_keys"
# Credential items written by versions before extractors, one per provider.
LEGACY_CREDENTIAL_ITEMS = {
    "gemini": "gemini_api_key",
    "openrouter": "openrouter_api_key",
    "openai_compatible": "openai_compatible_api_key",
}


class CredentialStoreUnavailable(RuntimeError):
    """Raised when the operating system has no usable credential backend."""


class VaultUnavailable(RuntimeError):
    """Raised when saved keys exist but cannot be read."""


def credentials_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg) / SERVICE_NAME

    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / SERVICE_NAME

    return Path.home() / ".config" / SERVICE_NAME


def legacy_preferences_path() -> Path:
    """Saved-key flags written by versions before extractors."""
    return credentials_dir() / "preferences.json"


def _keyring_module():
    try:
        import keyring
    except ImportError as exc:
        raise CredentialStoreUnavailable(
            "No operating-system credential store is available. "
            "Keys can still be used for this app session."
        ) from exc

    try:
        backend = keyring.get_keyring()
        priority = float(getattr(backend, "priority", 0))
    except Exception as exc:
        raise CredentialStoreUnavailable(
            "The operating-system credential store could not be opened. "
            "Keys can still be used for this app session."
        ) from exc
    if priority <= 0:
        raise CredentialStoreUnavailable(
            "No usable operating-system credential store was found. "
            "Keys can still be used for this app session."
        )
    return keyring


def credential_store_available() -> bool:
    try:
        _keyring_module()
    except CredentialStoreUnavailable:
        return False
    return True


class KeyVault:
    """Every extractor's API key, kept in one credential-store item.

    The item is read at most once per process, the first time a key is
    needed, and then served from memory. It is written only when a key is
    added, replaced or removed. Without a usable credential store, keys are
    kept in memory for this session only.
    """

    UNLOCK_MESSAGE = (
        "Couldn't unlock saved keys. Allow access when your computer asks, "
        "or replace the key in Settings."
    )

    def __init__(
        self,
        *,
        legacy_claims: Callable[[], dict[str, str]] | None = None,
        on_legacy_adopted: Callable[[dict[str, bool]], None] | None = None,
    ) -> None:
        """`legacy_claims` maps extractor ids to old per-provider credential items.

        When the vault is first opened those items are copied into it, and
        deleted only after the vault has been saved; `on_legacy_adopted` is
        then told which extractors received a key.
        """
        self._keys: dict[str, str] | None = None
        self._persistent: bool | None = None
        self._legacy_claims = legacy_claims
        self._on_legacy_adopted = on_legacy_adopted

    @property
    def persistent(self) -> bool:
        """Whether keys are saved in the credential store (checked without reading a key)."""
        if self._persistent is None:
            self._persistent = credential_store_available()
        return self._persistent

    def _load(self) -> dict[str, str]:
        if self._keys is not None:
            return self._keys
        if not self.persistent:
            self._keys = {}
            return self._keys
        try:
            raw = _keyring_module().get_password(SERVICE_NAME, VAULT_USERNAME)
            payload = json.loads(raw) if raw else {}
            keys = payload.get("keys") if isinstance(payload, dict) else None
            if raw and not isinstance(keys, dict):
                raise ValueError("The saved key vault is malformed.")
        except Exception as exc:
            raise VaultUnavailable(self.UNLOCK_MESSAGE) from exc
        self._keys = {str(name): str(value) for name, value in (keys or {}).items() if str(value).strip()}
        self._adopt_legacy_keys()
        return self._keys

    def _adopt_legacy_keys(self) -> None:
        claims = self._legacy_claims() if self._legacy_claims else {}
        if not claims:
            return
        keyring = _keyring_module()
        assert self._keys is not None
        for extractor_id, username in claims.items():
            if extractor_id in self._keys:
                continue
            try:
                value = str(keyring.get_password(SERVICE_NAME, username) or "").strip()
            except Exception:
                value = ""
            if value:
                self._keys[extractor_id] = value
        if not self._write():
            return
        for username in set(claims.values()):
            try:
                keyring.delete_password(SERVICE_NAME, username)
            except Exception:
                pass
        if self._on_legacy_adopted:
            self._on_legacy_adopted({extractor_id: extractor_id in self._keys for extractor_id in claims})

    def _write(self) -> bool:
        if not self.persistent:
            return False
        try:
            _keyring_module().set_password(
                SERVICE_NAME,
                VAULT_USERNAME,
                json.dumps({"version": 1, "keys": self._keys}),
            )
        except Exception:
            return False
        return True

    def get(self, extractor_id: str) -> str | None:
        return self._load().get(extractor_id)

    def cached(self, extractor_id: str) -> str | None:
        """Return a key only if the vault is already in memory; never reads the store."""
        return (self._keys or {}).get(extractor_id)

    def set(self, extractor_id: str, api_key: str) -> bool:
        """Save a key. Returns False when it is kept for this session only."""
        api_key = api_key.strip()
        if not api_key:
            raise ValueError("Cannot save an empty API key.")
        self._load()[extractor_id] = api_key
        return self._write()

    def delete(self, extractor_id: str) -> None:
        if self._load().pop(extractor_id, None) is not None:
            self._write()

    def secrets(self) -> list[str]:
        return list((self._keys or {}).values())
