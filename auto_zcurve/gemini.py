from __future__ import annotations

import json
import time
import unicodedata
from pathlib import Path
from typing import Any

from .llm import ExtractionResult
from .models import normalize_model_name
from .prompts import build_system_prompt
from .schema import ExtractionSchema, validate_extracted_json
from .config import normalize_service_tier


def _response_text(response: object) -> str:
    candidates = getattr(response, "candidates", None) or []
    parts = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            value = getattr(part, "text", None)
            if value:
                parts.append(value)
    joined = "".join(parts)
    if not joined.strip():
        raise RuntimeError("Gemini returned an empty text payload.")
    return joined


def _metadata_value(obj: object, *names: str) -> object | None:
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
        return None

    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _response_usage(response: object) -> dict[str, int | None]:
    usage = _metadata_value(response, "usage_metadata", "usageMetadata") or {}
    input_tokens = _int_or_none(_metadata_value(usage, "prompt_token_count", "promptTokenCount"))
    output_tokens = _int_or_none(_metadata_value(usage, "candidates_token_count", "candidatesTokenCount"))
    total_tokens = _int_or_none(_metadata_value(usage, "total_token_count", "totalTokenCount"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _parse_json_response(raw_text: str) -> tuple[dict[str, Any], str | None]:
    """Parse Gemini JSON, repairing malformed output while preserving both forms."""
    try:
        return json.loads(raw_text), None
    except json.JSONDecodeError as original_exc:
        try:
            from json_repair import repair_json
        except ImportError as exc:  # pragma: no cover - dependency is declared at install time
            error = RuntimeError(
                "Gemini returned invalid JSON and json-repair is not installed."
            )
            error.raw_response = raw_text  # type: ignore[attr-defined]
            raise error from exc
        repaired_text: str | None = None
        try:
            repaired_text = repair_json(raw_text)
            parsed = json.loads(repaired_text)
        except (TypeError, ValueError, json.JSONDecodeError) as repair_exc:
            error = RuntimeError(
                f"Gemini returned invalid JSON and json-repair could not repair it: {original_exc}"
            )
            error.raw_response = raw_text  # type: ignore[attr-defined]
            error.repaired_response = repaired_text  # type: ignore[attr-defined]
            raise error from repair_exc
        return parsed, repaired_text


def _generate(
    client: object,
    uploaded: object,
    model: str,
    prompt: str,
    response_schema: dict[str, Any],
    service_tier: str = "standard",
) -> tuple[dict[str, Any], str, str | None, dict[str, int | None]]:
    try:
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - preflight catches this
        raise RuntimeError("google-genai is not installed.") from exc

    response = client.models.generate_content(
        model=normalize_model_name(model),
        contents=[prompt, uploaded],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
            service_tier=normalize_service_tier(service_tier),
        ),
    )
    raw_text = _response_text(response)
    parsed, repaired_text = _parse_json_response(raw_text)
    return parsed, raw_text, repaired_text, _response_usage(response)


def extract_pdf(
    *,
    source_path: Path,
    source_name: str,
    api_key: str,
    primary_model: str,
    response_schema: dict[str, Any],
    schema_config: ExtractionSchema,
    instruction_path: Path,
    effect_definition: str | None,
    request_timeout_sec: int = 600,
    input_mode: str = "native_pdf",
    context_length: int | None = None,
    project_dir: Path | None = None,
    reasoning_effort: str | None = None,
    service_tier: str = "standard",
    endpoint_order: tuple[str, ...] = (),
) -> ExtractionResult:
    started = time.monotonic()
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed.") from exc

    client = genai.Client(api_key=api_key)
    prompt = build_system_prompt(schema_config, instruction_path, effect_definition)
    # Normalize filename to NFC then strip any remaining non-ASCII so the
    # Gemini SDK can encode it in the HTTP Content-Disposition header.
    nfc_name = unicodedata.normalize("NFC", source_path.name)
    try:
        nfc_name.encode("ascii")
        upload_name = nfc_name
    except UnicodeEncodeError:
        upload_name = unicodedata.normalize("NFKD", nfc_name).encode("ascii", "ignore").decode("ascii") or "document.pdf"
    with source_path.open("rb") as _fh:
        uploaded = client.files.upload(file=_fh, config={"display_name": upload_name, "mime_type": "application/pdf"})

    parsed, raw_text, repaired_text, usage = _generate(
        client, uploaded, primary_model, prompt, response_schema, service_tier
    )
    try:
        validate_extracted_json(parsed, schema_config)
    except Exception as exc:
        exc.raw_response = raw_text  # type: ignore[attr-defined]
        exc.repaired_response = repaired_text  # type: ignore[attr-defined]
        raise
    return ExtractionResult(
        source_path=source_path,
        source_name=source_name,
        status="ok",
        provider_used="gemini",
        model_used=normalize_model_name(primary_model),
        data=parsed,
        raw_json=raw_text,
        raw_response=raw_text,
        repaired_response=repaired_text,
        duration_sec=round(time.monotonic() - started, 3),
        input_tokens=_int_or_none(usage.get("input_tokens")),
        output_tokens=_int_or_none(usage.get("output_tokens")),
        total_tokens=_int_or_none(usage.get("total_tokens")),
        input_mode="native_pdf",
    )
