from __future__ import annotations

import json
import os
from pathlib import Path


SERVICE_NAME = "auto-zcurve"
KEY_NAME = "gemini_api_key"
LAST_PROJECT_KEY = "last_project_dir"


def credentials_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg) / SERVICE_NAME

    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / SERVICE_NAME

    return Path.home() / ".config" / SERVICE_NAME


def credentials_path() -> Path:
    return credentials_dir() / "credentials.json"


def load_saved_api_key() -> str | None:
    api_key = str(_load_credentials().get(KEY_NAME) or "").strip()
    return api_key or None


def save_api_key(api_key: str) -> Path:
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("Cannot save an empty Gemini API key.")

    data = _load_credentials()
    data[KEY_NAME] = api_key
    _save_credentials(data)
    return credentials_path()


def _load_credentials() -> dict:
    path = credentials_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_credentials(data: dict) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_last_project_dir() -> Path | None:
    value = str(_load_credentials().get(LAST_PROJECT_KEY) or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_dir() else None


def save_last_project_dir(project_dir: Path) -> None:
    data = _load_credentials()
    data[LAST_PROJECT_KEY] = str(project_dir.expanduser().resolve())
    _save_credentials(data)


def delete_saved_api_key() -> bool:
    data = _load_credentials()
    if KEY_NAME not in data:
        return False
    del data[KEY_NAME]
    _save_credentials(data)
    return True
