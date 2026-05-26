from __future__ import annotations

from dataclasses import dataclass


MAIN_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3.5-flash",
]

MAIN_MODEL_SET = set(MAIN_MODELS)


@dataclass
class ModelOption:
    name: str
    display_name: str
    description: str = ""


def strip_models_prefix(model: str) -> str:
    model = model.strip()
    return model[len("models/") :] if model.startswith("models/") else model


def normalize_model_name(model: str) -> str:
    stripped = strip_models_prefix(model)
    if not stripped:
        raise ValueError("A Gemini model name is required.")
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


def _looks_like_document_model(name: str) -> bool:
    return name.lower() in MAIN_MODEL_SET


def list_live_models(api_key: str) -> list[ModelOption]:
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
        if not _supports_generate_content(model) or not _looks_like_document_model(clean_name):
            continue
        display_name = str(_field(model, "display_name") or _field(model, "displayName") or clean_name)
        description = str(_field(model, "description") or "")
        options.append(ModelOption(clean_name, display_name, description))

    unique: dict[str, ModelOption] = {}
    for option in options:
        unique.setdefault(option.name, option)
    return sorted(unique.values(), key=lambda item: MAIN_MODELS.index(item.name.lower()))


def fallback_models() -> list[ModelOption]:
    return [ModelOption(name=model, display_name=model, description="Bundled fallback") for model in MAIN_MODELS]
