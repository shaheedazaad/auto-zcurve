from __future__ import annotations

from dataclasses import dataclass

from .providers import normalize_provider, provider_label


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

    return sorted(unique.values(), key=lambda option: option.name.lower())


def _list_openrouter_models(api_key: str, timeout_sec: int) -> list[ModelOption]:
    from .openrouter import list_models

    return sorted(
        (
            ModelOption(str(item["id"]), str(item.get("name") or item["id"]), provider="openrouter")
            for item in list_models(api_key=api_key or None, timeout_sec=timeout_sec)
            # Batch-only IDs can't run here, so they aren't suggested.
            if item.get("id") and not str(item["id"]).endswith(":batch")
        ),
        key=lambda option: option.name.lower(),
    )


def list_live_models(
    api_key: str,
    provider: str = "gemini",
    *,
    timeout_sec: int = 30,
) -> list[ModelOption]:
    """The provider's current models, offered as suggestions when an extractor is edited."""
    selected = normalize_provider(provider)
    if selected == "openai_compatible":
        return []
    if selected == "openrouter":
        return _list_openrouter_models(api_key, timeout_sec)
    return _list_gemini_models(api_key)


def validate_model_option(
    provider: str,
    model: str,
    api_key: str,
    *,
    timeout_sec: int = 30,
    context_length: int | None = None,
) -> ModelOption:
    """Check a model before a run and describe how sources reach it.

    OpenRouter models are checked against its live catalog. Gemini and
    OpenAI-compatible models are used as entered; the provider reports an
    unknown model when the first request is sent.
    """
    selected = normalize_provider(provider)
    normalized = normalize_model_name(model, selected)
    if selected == "openai_compatible":
        return ModelOption(normalized, normalized, provider=selected, input_mode="local_text", context_length=context_length)
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
                           supports_reasoning=bool(matched.get("reasoning")))
    return ModelOption(normalized, normalized, provider="gemini")


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
    source_format: str = "pdf",
) -> str:
    """Choose how one source reaches the model, from its source format and the provider."""
    selected = normalize_provider(provider)
    if source_format == "xml":
        from .sources import XML_TEXT_INPUT_MODE

        return XML_TEXT_INPUT_MODE
    if selected == "openai_compatible":
        return "local_text"
    return "cloudflare_pdf" if selected == "openrouter" else "native_pdf"
