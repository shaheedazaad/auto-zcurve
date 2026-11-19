from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from .credentials import credentials_dir
from .providers import normalize_provider


PDF_PARSERS = ("cloudflare-ai", "native")
PDF_PARSER_LABELS = {
    "cloudflare-ai": "Cloudflare AI",
    "native": "Model-native PDF",
}
# What a retry does when its extractor settings differ from the project's last run.
RETRY_EXTRACTOR_CHANGE_LABELS = {
    "rerun_all": "Re-run every article on the next run",
    "keep_results": "Keep other articles' results",
}


def normalize_retry_extractor_change(value: object) -> str:
    choice = str(value or "rerun_all").strip().lower()
    return choice if choice in RETRY_EXTRACTOR_CHANGE_LABELS else "rerun_all"


REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
REASONING_EFFORT_LABELS = {
    "none": "Off",
    "minimal": "Minimal",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "Extra high",
    "max": "Maximum",
}
SERVICE_TIERS = ("standard", "flex", "priority")
SERVICE_TIER_LABELS = {
    "standard": "Standard",
    "flex": "Flex (lower cost, variable latency)",
    "priority": "Priority (higher cost, lower latency)",
}
DEFAULTS = {
    "request_timeout_sec": 600,
    "parallel_requests": 1,
    "request_delay_sec": 30,
    "max_upload_size_mb": 128,
    "pdf_parser": "native",
    "reasoning_effort": "high",
    "service_tier": "flex",
}


def normalize_pdf_parser(value: object) -> str:
    parser = str(value or DEFAULTS["pdf_parser"]).strip().lower()
    if parser not in PDF_PARSERS:
        raise ValueError(f"Unsupported PDF parser: {value}")
    return parser


def normalize_reasoning_effort(value: object) -> str:
    effort = str(value or DEFAULTS["reasoning_effort"]).strip().lower()
    if effort not in REASONING_EFFORTS:
        raise ValueError(f"Unsupported reasoning level: {value}")
    return effort


def normalize_service_tier(value: object) -> str:
    tier = str(value or DEFAULTS["service_tier"]).strip().lower()
    if tier not in SERVICE_TIERS:
        raise ValueError(f"Unsupported service tier: {value}")
    return tier


@dataclass(frozen=True)
class AppSettings:
    """Application-wide settings that are not about a model; those belong to extractors."""

    pdf_parser: str = DEFAULTS["pdf_parser"]
    max_upload_size_mb: int = DEFAULTS["max_upload_size_mb"]
    retry_extractor_change: str = "rerun_all"


@dataclass
class RunSettings:
    """The concrete settings one run or retry uses, taken from its extractor."""

    primary_model: str
    request_timeout_sec: int = DEFAULTS["request_timeout_sec"]
    parallel_requests: int = DEFAULTS["parallel_requests"]
    request_delay_sec: int = DEFAULTS["request_delay_sec"]
    max_upload_size_mb: int = DEFAULTS["max_upload_size_mb"]
    provider: str = "gemini"
    pdf_parser: str = DEFAULTS["pdf_parser"]
    reasoning_effort: str = DEFAULTS["reasoning_effort"]
    service_tier: str = DEFAULTS["service_tier"]
    openrouter_provider: str = ""
    openrouter_allow_fallbacks: bool = True
    base_url: str = ""
    response_format: str = "json_schema"
    context_length: int | None = None
    extractor_id: str = ""
    # Every non-secret setting of the extractor when the run started.
    extractor: dict | None = None


OPENROUTER_PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9._-]+)?$")


def normalize_openrouter_provider(value: object) -> str:
    """Return an OpenRouter provider/endpoint slug such as `deepinfra/fp8`, or "" for automatic routing."""
    slug = str(value or "").strip().lower()
    if slug and not OPENROUTER_PROVIDER_PATTERN.fullmatch(slug):
        raise ValueError(f"Invalid OpenRouter provider: {value}")
    return slug


def normalize_endpoint_url(value: str) -> str:
    from .openai_compatible import normalize_base_url
    return normalize_base_url(value) if value.strip() else ""


def normalize_response_format(value: str) -> str:
    if value not in {"json_schema", "json_object", "none"}:
        raise ValueError("Unknown JSON output mode.")
    return value


def app_settings_path() -> Path:
    return credentials_dir() / "settings.json"


def load_app_settings() -> AppSettings:
    path = app_settings_path()
    if not path.is_file():
        return AppSettings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return AppSettings()
        return AppSettings(
            pdf_parser=normalize_pdf_parser(raw.get("pdf_parser")),
            max_upload_size_mb=max(
                1,
                min(int(raw.get("max_upload_size_mb") or DEFAULTS["max_upload_size_mb"]), 512),
            ),
            retry_extractor_change=normalize_retry_extractor_change(raw.get("retry_extractor_change")),
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return AppSettings()


def save_app_settings(settings: AppSettings) -> None:
    normalized = AppSettings(
        pdf_parser=normalize_pdf_parser(settings.pdf_parser),
        max_upload_size_mb=max(1, min(int(settings.max_upload_size_mb), 512)),
        retry_extractor_change=normalize_retry_extractor_change(settings.retry_extractor_change),
    )
    path = app_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "pdf_parser": normalized.pdf_parser,
                    "max_upload_size_mb": normalized.max_upload_size_mb,
                    "retry_extractor_change": normalized.retry_extractor_change,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def settings_path(project_dir: Path) -> Path:
    return project_dir / ".auto_zcurve" / "run_settings.json"


def legacy_effect_definition(project_dir: Path) -> str | None:
    """Read the retired per-run effect-definition setting for migration."""

    path = settings_path(project_dir)
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            return None
        legacy_value = raw.get("effect_definition")
        return legacy_value.strip() if isinstance(legacy_value, str) and legacy_value.strip() else None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def remove_legacy_effect_definition(project_dir: Path) -> None:
    """Remove the retired setting after its value has been migrated."""

    path = settings_path(project_dir)
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict) or "effect_definition" not in raw:
            return
        raw.pop("effect_definition")
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return


def _bounded_int(value: object, default: int, low: int, high: int) -> int:
    try:
        number = int(value) if value is not None and value != "" else default
    except (TypeError, ValueError):
        number = default
    return max(low, min(number, high))


def load_run_settings(project_dir: Path) -> RunSettings | None:
    """Read the settings of the project's last run or retry.

    Current files hold an extractor snapshot; files from before extractors
    hold flat provider and model fields and are still read. An unreadable or
    corrupt file counts as no previous run, so the next run or retry can
    replace it.
    """
    path = settings_path(project_dir)
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None

    snapshot = raw.get("extractor") if isinstance(raw.get("extractor"), dict) else None
    source = snapshot if snapshot is not None else raw
    primary_model = str(source.get("model" if snapshot is not None else "primary_model") or "").strip()
    if not primary_model:
        return None
    context_length = source.get("context_length")

    return RunSettings(
        primary_model=primary_model,
        provider=normalize_provider(source.get("provider") or "gemini"),
        request_timeout_sec=_bounded_int(source.get("request_timeout_sec"), DEFAULTS["request_timeout_sec"], 30, 3600),
        parallel_requests=_bounded_int(source.get("parallel_requests"), DEFAULTS["parallel_requests"], 1, 32),
        request_delay_sec=_bounded_int(source.get("request_delay_sec"), DEFAULTS["request_delay_sec"], 0, 3600),
        max_upload_size_mb=_bounded_int(raw.get("max_upload_size_mb"), DEFAULTS["max_upload_size_mb"], 1, 512),
        pdf_parser=normalize_pdf_parser(raw.get("pdf_parser")),
        reasoning_effort=normalize_reasoning_effort(source.get("reasoning_effort")),
        service_tier=normalize_service_tier(source.get("service_tier")),
        openrouter_provider=normalize_openrouter_provider(source.get("openrouter_provider")),
        openrouter_allow_fallbacks=source.get("openrouter_allow_fallbacks", True) is not False,
        base_url=str(source.get("base_url") or ""),
        response_format=str(source.get("response_format") or "json_schema"),
        context_length=int(context_length) if isinstance(context_length, int) and context_length > 0 else None,
        extractor_id=str(raw.get("extractor_id") or ""),
        extractor=snapshot,
    )


def save_run_settings(project_dir: Path, settings: RunSettings) -> None:
    """Record the settings a run used: its extractor's id and a snapshot of that extractor."""
    path = settings_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = dict(settings.extractor or {})
    if settings.extractor is None:
        # Settings built without an extractor snapshot still record what the
        # run used, so reading them back never substitutes defaults.
        snapshot.update({
            name: getattr(settings, name)
            for name in (
                "request_timeout_sec", "parallel_requests", "request_delay_sec", "reasoning_effort",
                "service_tier", "openrouter_provider", "openrouter_allow_fallbacks", "base_url",
                "response_format", "context_length",
            )
        })
    snapshot.update(provider=normalize_provider(settings.provider), model=settings.primary_model)
    payload = {
        "extractor_id": settings.extractor_id,
        "extractor": snapshot,
        "pdf_parser": normalize_pdf_parser(settings.pdf_parser),
        "max_upload_size_mb": settings.max_upload_size_mb,
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def selected_extractor_path(project_dir: Path) -> Path:
    return project_dir / ".auto_zcurve" / "extractor.json"


def load_selected_extractor_id(project_dir: Path) -> str:
    """The extractor chosen for the project's next run, or "" when none is chosen."""
    try:
        raw = json.loads(selected_extractor_path(project_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(raw.get("extractor_id") or "") if isinstance(raw, dict) else ""


def save_selected_extractor_id(project_dir: Path, extractor_id: str) -> None:
    path = selected_extractor_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Replace atomically so an interrupted write never leaves an empty file. A
    # unique name, so concurrent requests can't share the temporary file.
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps({"extractor_id": extractor_id}) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
