from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import MODEL_CONFIG
from .providers import normalize_provider, provider_label


MAIN_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
]

MAIN_MODEL_SET = set(MAIN_MODELS)
DEFAULT_MODEL = MAIN_MODELS[0]
DEFAULT_MODELS = {"gemini": DEFAULT_MODEL}
OPENROUTER_DEFAULT_PARALLEL_REQUESTS = 1
OPENROUTER_DEFAULT_REQUEST_DELAY_SEC = 0


@dataclass
class ModelOption:
    name: str
    display_name: str
    description: str = ""
    provider: str = "gemini"
    input_mode: str = "native_pdf"
    context_length: int | None = None
    supports_reasoning: bool = False
    endpoint_order: tuple[str, ...] = ()
    parallel_requests: int = 1
    request_delay_sec: int = 30


def _configured_models(path: Path | None = None) -> dict[str, dict[str, tuple[tuple[str, ...], int, int]]] | None:
    """Read the optional root model allowlist."""
    config_path = path or MODEL_CONFIG
    if not config_path.is_file():
        return None
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a runtime dependency
        raise RuntimeError("PyYAML is required to read models.yml.") from exc
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid model allowlist YAML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not read model allowlist {config_path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), dict):
        raise ValueError(f"{config_path} must contain a top-level `models` mapping.")

    configured: dict[str, dict[str, tuple[tuple[str, ...], int, int]]] = {}
    for provider, entries in raw["models"].items():
        selected = normalize_provider(str(provider))
        if not isinstance(entries, list):
            raise ValueError(f"models.{selected} in {config_path} must be a list.")
        provider_models: dict[str, tuple[tuple[str, ...], int, int]] = {}
        for index, entry in enumerate(entries, start=1):
            parallel_requests = 1
            request_delay_sec = 30
            if isinstance(entry, str):
                model_id = normalize_model_name(entry, selected)
                endpoints: object = []
            elif isinstance(entry, dict):
                model_id = normalize_model_name(str(entry.get("id") or ""), selected)
                endpoints = entry.get("endpoints") or []
                try:
                    parallel_requests = max(1, min(int(entry.get("parallel_requests", 1)), 32))
                    request_delay_sec = max(0, min(int(entry.get("request_delay_sec", 30)), 3600))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"models.{selected}[{index}] has invalid request defaults.") from exc
            else:
                raise ValueError(
                    f"models.{selected}[{index}] in {config_path} must be a model ID or mapping."
                )
            if not isinstance(endpoints, list) or any(
                not isinstance(value, str) or not value.strip() for value in endpoints
            ):
                raise ValueError(
                    f"models.{selected}[{index}].endpoints in {config_path} must be a list of endpoint tags."
                )
            if endpoints:
                raise ValueError(
                    f"models.{selected}[{index}] in {config_path} cannot define endpoints."
                )
            provider_models[model_id] = (tuple(value.strip() for value in endpoints), parallel_requests, request_delay_sec)
        configured[selected] = provider_models
    return configured


def _apply_model_allowlist(provider: str, options: list[ModelOption]) -> list[ModelOption]:
    configured = _configured_models()
    if configured is None:
        return options
    allowed = configured.get(normalize_provider(provider), {})
    filtered = []
    for option in options:
        if option.name not in allowed:
            continue
        option.endpoint_order, option.parallel_requests, option.request_delay_sec = allowed[option.name]
        filtered.append(option)
    return filtered


def strip_models_prefix(model: str) -> str:
    model = model.strip()
    return model[len("models/") :] if model.startswith("models/") else model


def normalize_model_name(model: str, provider: str = "gemini") -> str:
    selected = normalize_provider(provider)
    stripped = strip_models_prefix(model) if selected == "gemini" else model.strip()
    if not stripped:
        raise ValueError(f"A {provider_label(selected)} model name is required.")
    if selected == "openrouter" and stripped.endswith(":batch"):
        raise ValueError("OpenRouter batch-only models cannot run synchronous PDF extraction.")
    return stripped


def _field(model: object, name: str, default: object = None) -> object:
    if isinstance(model, dict):
        return model.get(name, default)
    return getattr(model, name, default)


def _supports_generate_content(model: object) -> bool:
    actions = _field(model, "supported_actions") or _field(model, "supportedActions") or []
    if not actions:
        return True
    return any(str(action).lower() == "generatecontent" for action in actions)


def _list_gemini_models(api_key: str) -> list[ModelOption]:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed.") from exc

    client = genai.Client(api_key=api_key)
    options: list[ModelOption] = []
    for model in client.models.list():
        name = str(_field(model, "name") or "").strip()
        if not name:
            continue
        clean_name = strip_models_prefix(name)
        if not _supports_generate_content(model):
            continue
        display_name = str(_field(model, "display_name") or _field(model, "displayName") or clean_name)
        description = str(_field(model, "description") or "")
        options.append(ModelOption(clean_name, display_name, description, "gemini"))

    unique: dict[str, ModelOption] = {}
    for option in options:
        unique.setdefault(option.name, option)

    def _sort_key(option: ModelOption) -> tuple[int, object]:
        lname = option.name.lower()
        if lname in MAIN_MODEL_SET:
            return (0, MAIN_MODELS.index(lname))
        return (1, lname)

    return sorted(unique.values(), key=_sort_key)


def list_live_models(
    api_key: str,
    provider: str = "gemini",
    *,
    force_refresh: bool = False,
    timeout_sec: int = 30,
) -> list[ModelOption]:
    selected = normalize_provider(provider)
    if selected == "openrouter":
        # Experimental OpenRouter models are intentionally manual-entry only.
        return []
    return _apply_model_allowlist(selected, _list_gemini_models(api_key))


def validate_model_option(
    provider: str,
    model: str,
    api_key: str,
    *,
    timeout_sec: int = 30,
) -> ModelOption:
    selected = normalize_provider(provider)
    normalized = normalize_model_name(model, selected)
    if selected == "openrouter":
        from .openrouter import list_models, model_input_mode

        matched = next((item for item in list_models(
            api_key=api_key,
            timeout_sec=timeout_sec,
            structured_outputs_only=False,
        ) if str(item.get("id") or "") == normalized), None)
        if matched is None:
            raise ValueError(
                f"OpenRouter model `{normalized}` was not found in the current model catalog. "
                "Check the provider/model spelling and whether that model variant is still available."
            )
        parameters = {str(item).lower() for item in matched.get("supported_parameters") or []}
        architecture = matched.get("architecture") if isinstance(matched.get("architecture"), dict) else {}
        inputs = {str(item).lower() for item in architecture.get("input_modalities") or []}
        if "structured_outputs" not in parameters:
            raise ValueError(
                f"OpenRouter model `{normalized}` is available, but it does not support strict structured outputs. "
                "auto-zcurve requires JSON Schema enforcement for reliable extraction; choose a model that lists `structured_outputs`."
            )
        if not ({"file", "text"} & inputs):
            raise ValueError("This OpenRouter model does not accept text input after PDF parsing.")
        if model_input_mode(matched) not in {"native_pdf", "cloudflare_pdf"}:
            raise ValueError("This OpenRouter model is not compatible with synchronous PDF extraction.")
        return ModelOption(normalized, str(matched.get("name") or normalized), provider="openrouter",
                           input_mode="cloudflare_pdf", context_length=matched.get("context_length"),
                           supports_reasoning=bool(matched.get("reasoning")),
                           parallel_requests=OPENROUTER_DEFAULT_PARALLEL_REQUESTS,
                           request_delay_sec=OPENROUTER_DEFAULT_REQUEST_DELAY_SEC)
    configured = _configured_models()
    if configured is not None and normalized not in configured.get(selected, {}):
        raise ValueError(
            f"{provider_label(selected)} model `{normalized}` is not allowed by {MODEL_CONFIG}."
        )
    configured_entry = (_configured_models() or {}).get(selected, {}).get(normalized)
    defaults = configured_entry or ((), 1, 30)
    return ModelOption(normalized, normalized, provider="gemini", endpoint_order=defaults[0], parallel_requests=defaults[1], request_delay_sec=defaults[2])


def validate_model(
    provider: str,
    model: str,
    api_key: str,
    *,
    timeout_sec: int = 30,
) -> str:
    return validate_model_option(
        provider,
        model,
        api_key,
        timeout_sec=timeout_sec,
    ).name


def resolve_input_mode(
    provider: str,
    model_option: ModelOption,
    pdf_parser: str,
) -> str:
    selected = normalize_provider(provider)
    return "cloudflare_pdf" if selected == "openrouter" else "native_pdf"


def fallback_models(provider: str = "gemini") -> list[ModelOption]:
    selected = normalize_provider(provider)
    if selected == "openrouter":
        return []
    return _apply_model_allowlist(selected, [
        ModelOption(name=model, display_name=model, description="Bundled fallback", provider="gemini")
        for model in MAIN_MODELS
    ])


def model_request_defaults(model: str, provider: str = "gemini") -> tuple[int, int]:
    normalized = normalize_model_name(model, provider)
    if normalize_provider(provider) == "openrouter":
        return OPENROUTER_DEFAULT_PARALLEL_REQUESTS, OPENROUTER_DEFAULT_REQUEST_DELAY_SEC
    for option in fallback_models(provider):
        if option.name == normalized:
            return option.parallel_requests, option.request_delay_sec
    return 1, 30
