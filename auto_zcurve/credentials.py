from __future__ import annotations

import json
import os
from pathlib import Path

from .providers import normalize_provider, provider_definition, provider_label


SERVICE_NAME = "auto-zcurve"
KEY_NAME = "gemini_api_key"
LAST_PROJECT_KEY = "last_project_dir"
SAVED_API_KEY_KEY = "saved_api_key_configured"
SAVED_API_KEY_KEYS = {
    "gemini": SAVED_API_KEY_KEY,
    "openrouter": "openrouter_saved_api_key_configured",
}


class CredentialStoreUnavailable(RuntimeError):
    """Raised when the operating system has no usable credential backend."""


def credentials_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg) / SERVICE_NAME

    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / SERVICE_NAME

    return Path.home() / ".config" / SERVICE_NAME


def credentials_path() -> Path:
    """Path for non-secret preferences.

    Kept as a public helper for compatibility. Provider keys are never written
    here; they live in the operating-system credential store.
    """

    return credentials_dir() / "preferences.json"


def _keyring_module():
    try:
        import keyring
    except ImportError as exc:
        raise CredentialStoreUnavailable(
            "No operating-system credential store is available. "
            "The key can still be used for this app session."
        ) from exc

    try:
        backend = keyring.get_keyring()
        priority = float(getattr(backend, "priority", 0))
    except Exception as exc:
        raise CredentialStoreUnavailable(
            "The operating-system credential store could not be opened. "
            "The key can still be used for this app session."
        ) from exc
    if priority <= 0:
        raise CredentialStoreUnavailable(
            "No usable operating-system credential store was found. "
            "The key can still be used for this app session."
        )
    return keyring


def credential_store_available() -> bool:
    try:
        _keyring_module()
    except CredentialStoreUnavailable:
        return False
    return True


def load_saved_api_key(provider: str = "gemini") -> str | None:
    selected = normalize_provider(provider)
    try:
        value = _keyring_module().get_password(SERVICE_NAME, provider_definition(selected).credential_key)
    except CredentialStoreUnavailable:
        return None
    except Exception:
        return None
    api_key = str(value or "").strip()
    return api_key or None


def save_api_key(api_key: str, provider: str = "gemini") -> str:
    selected = normalize_provider(provider)
    api_key = api_key.strip()
    if not api_key:
        raise ValueError(f"Cannot save an empty {provider_label(selected)} API key.")
    try:
        _keyring_module().set_password(
            SERVICE_NAME,
            provider_definition(selected).credential_key,
            api_key,
        )
    except CredentialStoreUnavailable:
        raise
    except Exception as exc:
        raise CredentialStoreUnavailable(
            "The operating-system credential store rejected the key. "
            "The key can still be used for this app session."
        ) from exc
    _set_saved_api_key_configured(True, selected)
    return "operating-system credential store"


def _load_preferences() -> dict:
    path = credentials_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_preferences(data: dict) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def saved_api_key_configured(provider: str = "gemini") -> bool:
    """Report saved-key intent without opening the credential store."""

    selected = normalize_provider(provider)
    return _load_preferences().get(SAVED_API_KEY_KEYS[selected]) is True


def _set_saved_api_key_configured(configured: bool, provider: str = "gemini") -> None:
    selected = normalize_provider(provider)
    preference_key = SAVED_API_KEY_KEYS[selected]
    data = _load_preferences()
    if configured:
        data[preference_key] = True
    else:
        data.pop(preference_key, None)
    _save_preferences(data)


def load_last_project_dir() -> Path | None:
    value = str(_load_preferences().get(LAST_PROJECT_KEY) or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_dir() else None


def save_last_project_dir(project_dir: Path) -> None:
    data = _load_preferences()
    data[LAST_PROJECT_KEY] = str(project_dir.expanduser().resolve())
    _save_preferences(data)


def delete_saved_api_key(provider: str = "gemini") -> bool:
    selected = normalize_provider(provider)
    try:
        _keyring_module().delete_password(
            SERVICE_NAME,
            provider_definition(selected).credential_key,
        )
    except Exception:
        _set_saved_api_key_configured(False, selected)
        return False
    _set_saved_api_key_configured(False, selected)
    return True
