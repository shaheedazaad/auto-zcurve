from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .credentials import credentials_dir
from .models import DEFAULT_MODEL, normalize_model_name
from .providers import normalize_provider


PDF_PARSERS = ("cloudflare-ai", "native")
PDF_PARSER_LABELS = {
    "cloudflare-ai": "Cloudflare AI",
    "native": "Model-native PDF",
}
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


def normalize_default_gemini_model(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_MODEL
    try:
        return normalize_model_name(text, "gemini")
    except ValueError:
        return DEFAULT_MODEL


def normalize_default_openrouter_model(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return normalize_model_name(text, "openrouter")


@dataclass(frozen=True)
class AppSettings:
    pdf_parser: str = DEFAULTS["pdf_parser"]
    reasoning_effort: str = DEFAULTS["reasoning_effort"]
    service_tier: str = DEFAULTS["service_tier"]
    request_timeout_sec: int = DEFAULTS["request_timeout_sec"]
    parallel_requests: int = DEFAULTS["parallel_requests"]
    request_delay_sec: int = DEFAULTS["request_delay_sec"]
    max_upload_size_mb: int = DEFAULTS["max_upload_size_mb"]
    default_gemini_model: str = DEFAULT_MODEL
    default_openrouter_model: str = ""


@dataclass
class RunSettings:
    primary_model: str
    request_timeout_sec: int = DEFAULTS["request_timeout_sec"]
    parallel_requests: int = DEFAULTS["parallel_requests"]
    request_delay_sec: int = DEFAULTS["request_delay_sec"]
    max_upload_size_mb: int = DEFAULTS["max_upload_size_mb"]
    effect_definition: str | None = None
    provider: str = "gemini"
    pdf_parser: str = DEFAULTS["pdf_parser"]
    reasoning_effort: str = DEFAULTS["reasoning_effort"]
    service_tier: str = DEFAULTS["service_tier"]


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
            reasoning_effort=normalize_reasoning_effort(raw.get("reasoning_effort")),
            service_tier=normalize_service_tier(raw.get("service_tier")),
            request_timeout_sec=max(
                30,
                min(int(raw.get("request_timeout_sec") or DEFAULTS["request_timeout_sec"]), 3600),
            ),
            parallel_requests=max(
                1,
                min(int(raw.get("parallel_requests") or DEFAULTS["parallel_requests"]), 32),
            ),
            request_delay_sec=max(0, min(int(raw.get("request_delay_sec") if raw.get("request_delay_sec") is not None else DEFAULTS["request_delay_sec"]), 3600)),
            max_upload_size_mb=max(
                1,
                min(int(raw.get("max_upload_size_mb") or DEFAULTS["max_upload_size_mb"]), 512),
            ),
            default_gemini_model=normalize_default_gemini_model(raw.get("default_gemini_model")),
            default_openrouter_model=normalize_default_openrouter_model(raw.get("default_openrouter_model")),
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return AppSettings()


def save_app_settings(settings: AppSettings) -> None:
    normalized = AppSettings(
        pdf_parser=normalize_pdf_parser(settings.pdf_parser),
        reasoning_effort=normalize_reasoning_effort(settings.reasoning_effort),
        service_tier=normalize_service_tier(settings.service_tier),
        request_timeout_sec=max(30, min(int(settings.request_timeout_sec), 3600)),
        parallel_requests=max(1, min(int(settings.parallel_requests), 32)),
        request_delay_sec=max(0, min(int(settings.request_delay_sec), 3600)),
        max_upload_size_mb=max(1, min(int(settings.max_upload_size_mb), 512)),
        default_gemini_model=normalize_default_gemini_model(settings.default_gemini_model),
        default_openrouter_model=normalize_default_openrouter_model(settings.default_openrouter_model),
    )
    path = app_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "pdf_parser": normalized.pdf_parser,
                    "reasoning_effort": normalized.reasoning_effort,
                    "service_tier": normalized.service_tier,
                    "request_timeout_sec": normalized.request_timeout_sec,
                    "parallel_requests": normalized.parallel_requests,
                    "request_delay_sec": normalized.request_delay_sec,
                    "max_upload_size_mb": normalized.max_upload_size_mb,
                    "default_gemini_model": normalized.default_gemini_model,
                    "default_openrouter_model": normalized.default_openrouter_model,
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
        provider=normalize_provider(raw.get("provider") or "gemini"),
        request_timeout_sec=int(raw.get("request_timeout_sec") or DEFAULTS["request_timeout_sec"]),
        parallel_requests=int(raw.get("parallel_requests") or DEFAULTS["parallel_requests"]),
        request_delay_sec=max(0, min(int(raw.get("request_delay_sec") if raw.get("request_delay_sec") is not None else DEFAULTS["request_delay_sec"]), 3600)),
        max_upload_size_mb=int(raw.get("max_upload_size_mb") or DEFAULTS["max_upload_size_mb"]),
        effect_definition=raw.get("effect_definition") or None,
        pdf_parser=normalize_pdf_parser(raw.get("pdf_parser")),
        reasoning_effort=normalize_reasoning_effort(raw.get("reasoning_effort")),
        service_tier=normalize_service_tier(raw.get("service_tier")),
    )


def save_run_settings(project_dir: Path, settings: RunSettings) -> None:
    path = settings_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "primary_model": settings.primary_model,
        "provider": normalize_provider(settings.provider),
        "request_timeout_sec": settings.request_timeout_sec,
        "parallel_requests": settings.parallel_requests,
        "request_delay_sec": settings.request_delay_sec,
        "max_upload_size_mb": settings.max_upload_size_mb,
        "effect_definition": settings.effect_definition,
        "pdf_parser": normalize_pdf_parser(settings.pdf_parser),
        "reasoning_effort": normalize_reasoning_effort(settings.reasoning_effort),
        "service_tier": normalize_service_tier(settings.service_tier),
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
