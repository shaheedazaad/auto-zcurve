from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llm import ExtractionResult
from .schema import ExtractionSchema


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


def extract_pdf(
    *,
    provider: str,
    source_path: Path,
    source_name: str,
    api_key: str,
    primary_model: str,
    response_schema: dict[str, Any],
    schema_config: ExtractionSchema,
    instruction_path: Path,
    effect_definition: str | None,
    request_timeout_sec: int,
    input_mode: str = "native_pdf",
    context_length: int | None = None,
    project_dir: Path | None = None,
    reasoning_effort: str | None = None,
    service_tier: str = "standard",
    endpoint_order: tuple[str, ...] = (),
) -> ExtractionResult:
    selected = normalize_provider(provider)
    module = importlib.import_module(PROVIDER_REGISTRY[selected].adapter_module)
    implementation = getattr(module, "extract_pdf")
    kwargs = dict(
        source_path=source_path,
        source_name=source_name,
        api_key=api_key,
        primary_model=primary_model,
        response_schema=response_schema,
        schema_config=schema_config,
        instruction_path=instruction_path,
        effect_definition=effect_definition,
        request_timeout_sec=request_timeout_sec,
        input_mode=input_mode,
        context_length=context_length,
        project_dir=project_dir,
        reasoning_effort=reasoning_effort,
        endpoint_order=endpoint_order,
    )
    if selected == "gemini":
        kwargs["service_tier"] = service_tier
    return implementation(**kwargs)
