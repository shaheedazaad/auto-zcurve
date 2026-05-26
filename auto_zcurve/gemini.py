from __future__ import annotations

import json
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import normalize_model_name
from .prompts import build_system_prompt
from .schema import ExtractionSchema, validate_extracted_json


@dataclass
class ExtractionResult:
    source_path: Path
    source_name: str
    status: str
    model_used: str | None = None
    data: dict[str, Any] | None = None
    raw_json: str | None = None
    error: str | None = None
    duration_sec: float = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    @property
    def effect_count(self) -> int:
        effects = (self.data or {}).get("effects") if self.data else None
        return len(effects) if isinstance(effects, list) else 0


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


def _generate(
    client: object,
    uploaded: object,
    model: str,
    prompt: str,
    response_schema: dict[str, Any],
) -> tuple[dict[str, Any], str, dict[str, int | None]]:
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
            temperature=0,
        ),
    )
    raw_text = _response_text(response)
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned invalid JSON: {exc}") from exc
    return parsed, raw_text, _response_usage(response)


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

    parsed, raw_text, usage = _generate(client, uploaded, primary_model, prompt, response_schema)
    validate_extracted_json(parsed, schema_config)
    return ExtractionResult(
        source_path=source_path,
        source_name=source_name,
        status="ok",
        model_used=normalize_model_name(primary_model),
        data=parsed,
        raw_json=raw_text,
        duration_sec=round(time.monotonic() - started, 3),
        input_tokens=_int_or_none(usage.get("input_tokens")),
        output_tokens=_int_or_none(usage.get("output_tokens")),
        total_tokens=_int_or_none(usage.get("total_tokens")),
    )
