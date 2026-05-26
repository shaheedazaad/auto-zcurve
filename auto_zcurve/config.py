from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


DEFAULTS = {
    "request_timeout_sec": 600,
    "parallel_requests": 10,
    "max_upload_size_mb": 128,
}


@dataclass
class RunSettings:
    primary_model: str
    request_timeout_sec: int = DEFAULTS["request_timeout_sec"]
    parallel_requests: int = DEFAULTS["parallel_requests"]
    max_upload_size_mb: int = DEFAULTS["max_upload_size_mb"]
    effect_definition: str | None = None


def settings_path(project_dir: Path) -> Path:
    return project_dir / ".auto_zcurve" / "run_settings.json"


def load_run_settings(project_dir: Path) -> RunSettings | None:
    path = settings_path(project_dir)
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    primary_model = str(raw.get("primary_model") or "").strip()
    if not primary_model:
        return None

    return RunSettings(
        primary_model=primary_model,
        request_timeout_sec=int(raw.get("request_timeout_sec") or DEFAULTS["request_timeout_sec"]),
        parallel_requests=int(raw.get("parallel_requests") or DEFAULTS["parallel_requests"]),
        max_upload_size_mb=int(raw.get("max_upload_size_mb") or DEFAULTS["max_upload_size_mb"]),
        effect_definition=raw.get("effect_definition") or None,
    )


def save_run_settings(project_dir: Path, settings: RunSettings) -> None:
    path = settings_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "primary_model": settings.primary_model,
        "request_timeout_sec": settings.request_timeout_sec,
        "parallel_requests": settings.parallel_requests,
        "max_upload_size_mb": settings.max_upload_size_mb,
        "effect_definition": settings.effect_definition,
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
