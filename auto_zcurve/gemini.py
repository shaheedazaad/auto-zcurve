from __future__ import annotations

import time
import unicodedata
from pathlib import Path
from typing import Any

from .llm import (
    ExtractionResult,
    extraction_review_reasons,
    parse_json_with_repair,
    review_status,
)
from .models import normalize_model_name
from .prompts import build_system_prompt
from .schema import ExtractionSchema, validate_extracted_json
from .config import normalize_service_tier
from .openai_compatible import ExtractionCancelled
from .openrouter import MAX_COMPLETION_ATTEMPTS, _retry_delay, _retryable_status

# Each Gemini request (file upload, then generation) gets this many attempts in
# total. The SDK's own retries stay off (its default) so this bound holds.
MAX_REQUEST_ATTEMPTS = MAX_COMPLETION_ATTEMPTS
_CANCEL_POLL_SEC = 0.1


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


# FINISH_REASON_UNSPECIFIED is the API's unset default, so it counts as missing.
_NORMAL_FINISH_REASONS = ("STOP", "FINISH_REASON_UNSPECIFIED")


def _finish_reason_name(value: object) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    text = str(name if isinstance(name, str) else value).strip()
    return text.removeprefix("FinishReason.") or None


def _response_finish_reasons(response: object) -> list[str]:
    """Return each candidate's finish reason that is present, in order and without duplicates."""
    reasons: list[str] = []
    for candidate in _metadata_value(response, "candidates") or []:
        reason = _finish_reason_name(_metadata_value(candidate, "finish_reason", "finishReason"))
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons


def _response_usage(response: object) -> dict[str, int | None]:
    usage = _metadata_value(response, "usage_metadata", "usageMetadata") or {}
    input_tokens = _int_or_none(_metadata_value(usage, "prompt_token_count", "promptTokenCount"))
    output_tokens = _int_or_none(_metadata_value(usage, "candidates_token_count", "candidatesTokenCount"))
    # Gemini bills thinking tokens as output and already includes them in its total.
    thinking_tokens = _int_or_none(_metadata_value(usage, "thoughts_token_count", "thoughtsTokenCount"))
    if output_tokens is not None and thinking_tokens is not None:
        output_tokens += thinking_tokens
    total_tokens = _int_or_none(_metadata_value(usage, "total_token_count", "totalTokenCount"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _retryable(exc: BaseException) -> bool:
    """Retry rate limits, server errors and dropped connections only."""
    import httpx
    from google.genai import errors

    if isinstance(exc, errors.APIError):
        return _retryable_status(exc.code)
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))


def _retry_after_sec(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        return float(headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        return None


def _raise_if_cancelled(cancellation_event: object | None) -> None:
    if cancellation_event is not None and cancellation_event.is_set():
        raise ExtractionCancelled("Extraction cancelled by user.")


def _wait_for_retry(delay_sec: float, cancellation_event: object | None) -> None:
    """Sleep before the next attempt, stopping early if the run is cancelled."""
    deadline = time.monotonic() + delay_sec
    while True:
        _raise_if_cancelled(cancellation_event)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, _CANCEL_POLL_SEC))


def _with_retries(request: Any, cancellation_event: object | None) -> Any:
    attempt = 1
    while True:
        _raise_if_cancelled(cancellation_event)
        try:
            return request()
        except Exception as exc:
            if attempt >= MAX_REQUEST_ATTEMPTS or not _retryable(exc):
                raise
            exc.retry_after_sec = _retry_after_sec(exc)  # type: ignore[attr-defined]
            _wait_for_retry(_retry_delay(attempt, exc), cancellation_event)
            attempt += 1


def _parse_json_response(raw_text: str) -> tuple[dict[str, Any], str | None]:
    """Parse Gemini JSON, repairing malformed output while preserving both forms."""
    return parse_json_with_repair(raw_text, "Gemini")


def _generate(
    client: object,
    uploaded: object,
    model: str,
    prompt: str,
    response_schema: dict[str, Any],
    service_tier: str = "standard",
) -> tuple[dict[str, Any], str, str | None, dict[str, int | None], list[str]]:
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
    return parsed, raw_text, repaired_text, _response_usage(response), _response_finish_reasons(response)


def extract_pdf(
    *,
    source_path: Path,
    source_name: str,
    api_key: str,
    primary_model: str,
    response_schema: dict[str, Any],
    schema_config: ExtractionSchema,
    instruction_path: Path,
    request_timeout_sec: int = 600,
    input_mode: str = "native_pdf",
    context_length: int | None = None,
    project_dir: Path | None = None,
    reasoning_effort: str | None = None,
    service_tier: str = "standard",
    endpoint_order: tuple[str, ...] = (),
    cancellation_event: object | None = None,
) -> ExtractionResult:
    started = time.monotonic()
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai is not installed.") from exc

    client = genai.Client(api_key=api_key)
    prompt = build_system_prompt(schema_config, instruction_path)
    # Normalize filename to NFC then strip any remaining non-ASCII so the
    # Gemini SDK can encode it in the HTTP Content-Disposition header.
    nfc_name = unicodedata.normalize("NFC", source_path.name)
    try:
        nfc_name.encode("ascii")
        upload_name = nfc_name
    except UnicodeEncodeError:
        upload_name = unicodedata.normalize("NFKD", nfc_name).encode("ascii", "ignore").decode("ascii") or "document.pdf"

    def upload() -> object:
        # Reopen per attempt so a retry sends the whole file again.
        with source_path.open("rb") as _fh:
            return client.files.upload(
                file=_fh, config={"display_name": upload_name, "mime_type": "application/pdf"}
            )

    uploaded = _with_retries(upload, cancellation_event)
    parsed, raw_text, repaired_text, usage, finish_reasons = _with_retries(
        lambda: _generate(client, uploaded, primary_model, prompt, response_schema, service_tier),
        cancellation_event,
    )
    try:
        validate_extracted_json(parsed, schema_config)
    except Exception as exc:
        exc.raw_response = raw_text  # type: ignore[attr-defined]
        exc.repaired_response = repaired_text  # type: ignore[attr-defined]
        raise
    review_reasons = extraction_review_reasons(repaired_text, finish_reasons, _NORMAL_FINISH_REASONS)
    return ExtractionResult(
        source_path=source_path,
        source_name=source_name,
        status=review_status(review_reasons),
        review_reasons=review_reasons,
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
