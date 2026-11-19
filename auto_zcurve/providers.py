from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llm import ExtractionResult
from .schema import ExtractionSchema
from .sources import XML_TEXT_INPUT_MODE, check_context_length, estimate_tokens, read_article_text, source_format


@dataclass(frozen=True)
class ProviderDefinition:
    name: str
    label: str
    environment_key: str
    credential_key: str
    adapter_module: str


PROVIDER_REGISTRY = {
    "gemini": ProviderDefinition(
        name="gemini",
        label="Gemini",
        environment_key="GEMINI_API_KEY",
        credential_key="gemini_api_key",
        adapter_module="auto_zcurve.gemini",
    ),
    "openrouter": ProviderDefinition(
        name="openrouter",
        label="OpenRouter (Experimental)",
        environment_key="OPENROUTER_API_KEY",
        credential_key="openrouter_api_key",
        adapter_module="auto_zcurve.openrouter",
    ),
}
PROVIDER_REGISTRY["openai_compatible"] = ProviderDefinition(
    name="openai_compatible", label="OpenAI-compatible",
    environment_key="OPENAI_COMPATIBLE_API_KEY", credential_key="openai_compatible_api_key",
    adapter_module="auto_zcurve.openai_compatible",
)
PROVIDERS = tuple(PROVIDER_REGISTRY)
PROVIDER_LABELS = {name: definition.label for name, definition in PROVIDER_REGISTRY.items()}


def normalize_provider(provider: str | None) -> str:
    value = str(provider or "gemini").strip().lower()
    if value not in PROVIDERS:
        raise ValueError(f"Unsupported LLM provider: {provider}")
    return value


def provider_label(provider: str | None) -> str:
    return PROVIDER_LABELS[normalize_provider(provider)]


def provider_definition(provider: str | None) -> ProviderDefinition:
    return PROVIDER_REGISTRY[normalize_provider(provider)]


def extract_document(
    *,
    provider: str,
    source_path: Path,
    source_name: str,
    api_key: str,
    primary_model: str,
    response_schema: dict[str, Any],
    schema_config: ExtractionSchema,
    instruction_path: Path,
    request_timeout_sec: int,
    input_mode: str = "native_pdf",
    context_length: int | None = None,
    project_dir: Path | None = None,
    reasoning_effort: str | None = None,
    service_tier: str = "standard",
    endpoint_order: tuple[str, ...] = (),
    cancellation_event: object | None = None,
    openrouter_provider: str = "",
    openrouter_allow_fallbacks: bool = True,
) -> ExtractionResult:
    """Extract one source of any source format with the selected provider.

    An XML source is read here as article text and checked against the
    model's known context length, so an oversized article fails before any
    request is sent. Its text is never truncated.
    """
    selected = normalize_provider(provider)
    fmt = source_format(source_path) or "pdf"
    article_text = None
    estimated_input_tokens = None
    if fmt == "xml":
        if input_mode != XML_TEXT_INPUT_MODE:
            raise ValueError(f"XML sources must be sent as article text, not with input mode {input_mode}.")
        article_text = read_article_text(source_path)
        estimated_input_tokens = estimate_tokens(article_text)
        check_context_length(estimated_input_tokens, context_length)
    module = importlib.import_module(PROVIDER_REGISTRY[selected].adapter_module)
    implementation = getattr(module, "extract_document")
    kwargs = dict(
        source_path=source_path,
        source_name=source_name,
        api_key=api_key,
        primary_model=primary_model,
        response_schema=response_schema,
        schema_config=schema_config,
        instruction_path=instruction_path,
        request_timeout_sec=request_timeout_sec,
        input_mode=input_mode,
        context_length=context_length,
        project_dir=project_dir,
        reasoning_effort=reasoning_effort,
        endpoint_order=endpoint_order,
    )
    if article_text is not None:
        kwargs["article_text"] = article_text
    if selected == "gemini":
        kwargs["service_tier"] = service_tier
        kwargs["cancellation_event"] = cancellation_event
    if selected == "openrouter":
        kwargs["preferred_provider"] = openrouter_provider
        kwargs["allow_fallbacks"] = openrouter_allow_fallbacks
    result = implementation(**kwargs)
    if estimated_input_tokens is not None and result.estimated_input_tokens is None:
        result.estimated_input_tokens = estimated_input_tokens
    return result
