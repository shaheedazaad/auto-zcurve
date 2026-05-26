from __future__ import annotations

import os
from pathlib import Path

from .credentials import load_saved_api_key


def load_dotenv(path: Path, overwrite: bool = False) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and (overwrite or not os.environ.get(key)):
            os.environ[key] = value


def resolve_api_key(project_dir: Path | None = None, explicit_key: str | None = None) -> str:
    explicit_key = (explicit_key or "").strip()
    if explicit_key:
        return explicit_key

    if project_dir is not None:
        load_dotenv(project_dir / ".env")
    load_dotenv(Path.cwd() / ".env")

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key:
        return api_key

    saved_key = load_saved_api_key()
    if saved_key:
        return saved_key

    raise RuntimeError(
        "GEMINI_API_KEY is required. Enter it in the CLI, set it in the environment or .env, or save it in the GUI."
    )
