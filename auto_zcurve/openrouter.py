from __future__ import annotations

import base64
import json
import math
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import normalize_reasoning_effort
from .llm import (
    MAX_PROVIDER_ATTEMPTS,
    ExtractionResult,
    extraction_review_reasons,
    parse_json_with_repair,
    retry_delay,
    retryable_status,
    review_status,
)
from .prompts import build_system_prompt
from .schema import ExtractionSchema, normalize_extracted_json, validate_extracted_json


API_BASE = "https://openrouter.ai/api/v1"
USER_AGENT = "auto-zcurve"
LOCAL_OUTPUT_TOKEN_RESERVE = 32_768
LOCAL_CONTEXT_SAFETY_RATIO = 0.85
MAX_COMPLETION_ATTEMPTS = MAX_PROVIDER_ATTEMPTS
OPENROUTER_PDF_ENGINES = ("cloudflare-ai", "mistral-ocr")
# OpenAI-style finish reasons; anything else (length, content_filter, ...) needs review.
NORMAL_FINISH_REASONS = ("stop",)


class _OpenRouterRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_sec: float | None = None,
        raw_response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_sec = retry_after_sec
        self.raw_response = raw_response

    @property
    def retryable(self) -> bool:
        return self.status_code is None or retryable_status(self.status_code)


def _request_json(
    url: str,
    *,
    api_key: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout_sec: int = 60,
) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    data = None
    method = "GET"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if payload is not None:
        method = "POST"
        headers["Content-Type"] = "application/json"
        headers["X-Title"] = "auto-zcurve"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            parsed = json.load(response)
    except urllib.error.HTTPError as exc:
        raw_response = None
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace"))
            raw_response = detail if isinstance(detail, dict) else {"body": detail}
            error = detail.get("error") if isinstance(detail, dict) else None
            message = (
                error.get("message") if isinstance(error, dict) else str(error or "")
            ) or (detail.get("message") if isinstance(detail, dict) else "") or str(detail)
        except Exception:
            message = exc.reason or str(exc)
        retry_after = None
        try:
            retry_after = float(exc.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            pass
        raise _OpenRouterRequestError(
            f"OpenRouter request failed ({exc.code}): {message}",
            status_code=exc.code,
            retry_after_sec=retry_after,
            raw_response=raw_response,
        ) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise _OpenRouterRequestError(f"OpenRouter request failed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenRouter returned an invalid response payload.")
    return parsed


def list_models(
    *,
    api_key: str | None = None,
    timeout_sec: int = 30,
    structured_outputs_only: bool = True,
) -> list[dict[str, Any]]:
    query = "output_modalities=text"
    if structured_outputs_only:
        query += "&supported_parameters=structured_outputs"
    payload = _request_json(
        f"{API_BASE}/models?{query}",
        api_key=api_key,
        timeout_sec=timeout_sec,
    )
    models = payload.get("data")
    if not isinstance(models, list):
        raise RuntimeError("OpenRouter returned an invalid model catalog.")
    return [model for model in models if isinstance(model, dict)]


def list_model_endpoints(
    model_id: str,
    *,
    api_key: str | None = None,
    timeout_sec: int = 30,
) -> list[dict[str, str]]:
    """List the provider endpoints serving a model that can return strict structured output."""
    model_path = "/".join(urllib.parse.quote(part, safe="") for part in model_id.split(":", 1)[0].split("/"))
    payload = _request_json(f"{API_BASE}/models/{model_path}/endpoints", api_key=api_key, timeout_sec=timeout_sec)
    data = payload.get("data")
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(endpoints, list):
        raise RuntimeError("OpenRouter returned an invalid endpoint list.")
    listed: dict[str, dict[str, str]] = {}
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        tag = str(endpoint.get("tag") or "").strip().lower()
        parameters = {str(value).lower() for value in endpoint.get("supported_parameters") or []}
        if not tag or "structured_outputs" not in parameters or tag in listed:
            continue
        details = [part for part in (tag.partition("/")[2], str(endpoint.get("quantization") or "")) if part and part != "unknown"]
        name = str(endpoint.get("provider_name") or tag)
        listed[tag] = {"tag": tag, "name": f"{name} ({', '.join(dict.fromkeys(details))})" if details else name}
    return sorted(listed.values(), key=lambda item: item["name"].lower())


def supports_native_structured_pdf(model: dict[str, Any]) -> bool:
    return model_input_mode(model) == "native_pdf"


def model_input_mode(model: dict[str, Any]) -> str | None:
    model_id = str(model.get("id") or "").strip().lower()
    parameters = {str(value).lower() for value in model.get("supported_parameters") or []}
    architecture = model.get("architecture") if isinstance(model.get("architecture"), dict) else {}
    inputs = {str(value).lower() for value in architecture.get("input_modalities") or []}
    outputs = {str(value).lower() for value in architecture.get("output_modalities") or ["text"]}
    synchronous_structured_text = (
        bool(model_id)
        and not model_id.endswith(":batch")
        and "structured_outputs" in parameters
        and "text" in outputs
    )
    if not synchronous_structured_text:
        return None
    if "file" in inputs:
        return "native_pdf"
    # OpenRouter's file-parser plugin converts the PDF to model-compatible text
    # before dispatch. Text-only models can therefore use the Cloudflare/Mistral
    # parser path even though their model metadata does not advertise `file`.
    if "text" in inputs:
        return "cloudflare_pdf"
    return None


def strict_response_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert the project schema to OpenRouter's strict JSON Schema subset."""

    def convert(node: object, *, nullable: bool = False) -> object:
        if not isinstance(node, dict):
            return node
        out = {key: convert(value) for key, value in node.items() if key not in {"properties", "required", "items"}}
        node_type = node.get("type")
        if node_type == "object":
            properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            originally_required = set(node.get("required") or [])
            out["properties"] = {
                name: convert(value, nullable=name not in originally_required)
                for name, value in properties.items()
            }
            out["required"] = list(properties)
            out["additionalProperties"] = False
        elif node_type == "array" and "items" in node:
            out["items"] = convert(node["items"])
        if nullable:
            if isinstance(node_type, str):
                out["type"] = [node_type, "null"]
            elif isinstance(node_type, list) and "null" not in node_type:
                out["type"] = [*node_type, "null"]
        return out

    converted = convert(schema)
    if not isinstance(converted, dict):  # pragma: no cover - schema is always an object
        raise ValueError("The extraction response schema must be an object.")
    return converted


def _message_text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
        )
    return ""


def _usage(payload: dict[str, Any]) -> dict[str, int | None]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}

    def integer(name: str) -> int | None:
        try:
            value = usage.get(name)
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "input_tokens": integer("prompt_tokens"),
        "output_tokens": integer("completion_tokens"),
        "total_tokens": integer("total_tokens"),
    }


def _embedded_error(payload: dict[str, Any]) -> tuple[str, bool] | None:
    choices = payload.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    error = payload.get("error")
    if not isinstance(error, dict):
        error = choice.get("error")
    if not isinstance(error, dict) and choice.get("finish_reason") != "error":
        return None
    error = error if isinstance(error, dict) else {}
    code = error.get("code")
    metadata = error.get("metadata") if isinstance(error.get("metadata"), dict) else {}
    error_type = str(metadata.get("error_type") or "").strip()
    message = str(error.get("message") or "Provider generation failed.").strip()
    labels = [str(value) for value in (code, error_type) if value not in {None, ""}]
    detail = (
        f"OpenRouter generation failed ({'/'.join(labels)}): {message}"
        if labels
        else f"OpenRouter generation failed: {message}"
    )
    retryable_types = {
        "rate_limit_exceeded",
        "provider_unavailable",
        "server_error",
        "timeout",
    }
    retryable = (
        retryable_status(code)
        if code is not None
        else error_type in retryable_types or not error_type
    )
    return detail, retryable


def _empty_response_error(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    finish_reason = str(choice.get("finish_reason") or "unknown")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    completion_details = (
        usage.get("completion_tokens_details")
        if isinstance(usage.get("completion_tokens_details"), dict)
        else {}
    )
    reasoning_tokens = completion_details.get("reasoning_tokens")
    suffix = f", reasoning tokens: {reasoning_tokens}" if reasoning_tokens is not None else ""
    return (
        "OpenRouter returned no final text "
        f"(finish reason: {finish_reason}, completion tokens: {usage.get('completion_tokens', 'unknown')}{suffix})."
    )


def choice_finish_reason(choice: object) -> str | None:
    """Return an OpenAI-style choice's finish reason, or None when it is absent."""
    value = choice.get("finish_reason") if isinstance(choice, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def _add_usage(
    total: dict[str, int | None],
    current: dict[str, int | None],
) -> None:
    for name, value in current.items():
        if value is not None:
            total[name] = int(total.get(name) or 0) + value


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
    endpoint_order: tuple[str, ...] = (),
    preferred_provider: str = "",
    allow_fallbacks: bool = True,
) -> ExtractionResult:
    started = time.monotonic()
    prompt = build_system_prompt(schema_config, instruction_path)
    parsed_document = None
    estimated_input_tokens = None
    if input_mode in {"native_pdf", "cloudflare_pdf"}:
        encoded_pdf = base64.b64encode(source_path.read_bytes()).decode("ascii")
        user_content = [
            {"type": "text", "text": prompt},
            {
                "type": "file",
                "file": {
                    "filename": source_path.name,
                    "file_data": f"data:application/pdf;base64,{encoded_pdf}",
                },
            },
        ]
    else:
        raise RuntimeError(f"Unsupported OpenRouter input mode: {input_mode}")

    payload = {
        "model": primary_model,
        "messages": [
            {
                "role": "user",
                "content": user_content,
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_config.name,
                "strict": True,
                "schema": strict_response_schema(response_schema),
            },
        },
        "provider": {"require_parameters": True},
        "stream": False,
    }
    if endpoint_order:
        payload["provider"].update(
            {
                "order": list(endpoint_order),
                "only": list(endpoint_order),
                "allow_fallbacks": False,
            }
        )
    elif preferred_provider:
        payload["provider"].update({"order": [preferred_provider], "allow_fallbacks": allow_fallbacks})
    elif not allow_fallbacks:
        payload["provider"]["allow_fallbacks"] = False
    if reasoning_effort is not None:
        payload["reasoning"] = {"effort": normalize_reasoning_effort(reasoning_effort)}
    engine = "native" if input_mode == "native_pdf" else OPENROUTER_PDF_ENGINES[0]
    payload["plugins"] = [{"id": "file-parser", "pdf": {"engine": engine}}]
    if input_mode == "cloudflare_pdf":
        payload["max_tokens"] = LOCAL_OUTPUT_TOKEN_RESERVE
    usage: dict[str, int | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    raw_text = ""
    repaired_text: str | None = None
    parsed: object = None
    provider_responses: list[dict[str, Any]] = []
    last_error: RuntimeError | ValueError | None = None
    def should_retry(attempt: int, retryable: bool = True) -> bool:
        # The first Cloudflare parser failure gets one guaranteed Mistral OCR
        # attempt, including non-retryable HTTP/parser errors.
        return attempt < MAX_COMPLETION_ATTEMPTS and (
            retryable or (input_mode == "cloudflare_pdf" and attempt == 1)
        )

    for attempt in range(1, MAX_COMPLETION_ATTEMPTS + 1):
        if input_mode == "cloudflare_pdf":
            payload["plugins"] = [{"id": "file-parser", "pdf": {
                "engine": OPENROUTER_PDF_ENGINES[min(attempt - 1, len(OPENROUTER_PDF_ENGINES) - 1)]
            }}]
        try:
            response = _request_json(
                f"{API_BASE}/chat/completions",
                api_key=api_key,
                payload=payload,
                timeout_sec=request_timeout_sec,
            )
        except _OpenRouterRequestError as exc:
            if exc.raw_response is not None:
                provider_responses.append({"attempt": attempt, "response": exc.raw_response})
            exc.provider_responses = provider_responses
            last_error = exc
            if should_retry(attempt, exc.retryable):
                time.sleep(retry_delay(attempt, exc))
                continue
            raise

        provider_responses.append({"attempt": attempt, "response": response})
        _add_usage(usage, _usage(response))
        response_error = _embedded_error(response)
        if response_error is not None:
            message, retryable = response_error
            last_error = RuntimeError(message)
            last_error.provider_responses = provider_responses
            if should_retry(attempt, retryable):
                time.sleep(retry_delay(attempt))
                continue
            raise last_error

        choices = response.get("choices")
        raw_text = (
            _message_text(choices[0].get("message"))
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else ""
        )
        if not raw_text.strip():
            last_error = RuntimeError(_empty_response_error(response))
            last_error.provider_responses = provider_responses
            if should_retry(attempt):
                time.sleep(retry_delay(attempt))
                continue
            raise last_error
        try:
            parsed, repaired_text = parse_json_with_repair(raw_text, "OpenRouter")
        except RuntimeError as exc:
            # Unrepairable output: retry for a fresh response.
            last_error = exc
            last_error.provider_responses = provider_responses
            if should_retry(attempt):
                time.sleep(retry_delay(attempt))
                continue
            raise

        normalize_extracted_json(parsed, schema_config)
        try:
            validate_extracted_json(parsed, schema_config, provider_name="OpenRouter")
        except ValueError as exc:
            last_error = exc
            last_error.provider_responses = provider_responses
            last_error.raw_response = raw_text
            last_error.repaired_response = repaired_text
            if should_retry(attempt):
                time.sleep(retry_delay(attempt))
                continue
            raise
        break
    else:  # pragma: no cover - every unsuccessful branch raises on its last attempt
        raise last_error or RuntimeError("OpenRouter did not complete the extraction.")

    if not isinstance(parsed, dict):  # pragma: no cover - validation guarantees this
        raise RuntimeError("OpenRouter returned JSON that is not a top-level object.")
    # Only the final, successful attempt is checked; earlier attempts were retried.
    review_reasons = extraction_review_reasons(
        repaired_text, [choice_finish_reason(choices[0])], NORMAL_FINISH_REASONS
    )
    return ExtractionResult(
        source_path=source_path,
        source_name=source_name,
        status=review_status(review_reasons),
        review_reasons=review_reasons,
        provider_used="openrouter",
        model_used=str(response.get("model") or primary_model),
        data=parsed,
        raw_json=raw_text,
        raw_response=raw_text,
        repaired_response=repaired_text,
        provider_responses=provider_responses,
        duration_sec=round(time.monotonic() - started, 3),
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        total_tokens=usage["total_tokens"],
        input_mode=input_mode,
        parser_name=parsed_document.parser_name if parsed_document else None,
        parser_version=parsed_document.parser_version if parsed_document else None,
        parser_config_version=parsed_document.parser_config_version if parsed_document else None,
        source_pdf_sha256=parsed_document.source_sha256 if parsed_document else None,
        parsed_document_sha256=parsed_document.document_sha256 if parsed_document else None,
        parser_page_count=parsed_document.page_count if parsed_document else None,
        parser_mean_grade=parsed_document.mean_grade if parsed_document else None,
        parser_low_grade=parsed_document.low_grade if parsed_document else None,
        parser_warnings=parsed_document.warnings if parsed_document else (),
        parser_cache_path=parsed_document.cache_markdown_path if parsed_document else None,
        parser_duration_sec=parsed_document.duration_sec if parsed_document else None,
        estimated_input_tokens=estimated_input_tokens,
    )
