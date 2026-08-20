from __future__ import annotations

import base64
import json
import math
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import normalize_reasoning_effort
from .llm import ExtractionResult
from .prompts import build_system_prompt
from .schema import ExtractionSchema, normalize_extracted_json, validate_extracted_json


API_BASE = "https://openrouter.ai/api/v1"
USER_AGENT = "auto-zcurve"
LOCAL_OUTPUT_TOKEN_RESERVE = 32_768
LOCAL_CONTEXT_SAFETY_RATIO = 0.85
MAX_COMPLETION_ATTEMPTS = 3
RETRY_BASE_DELAY_SEC = 1.0
OPENROUTER_PDF_ENGINES = ("cloudflare-ai", "mistral-ocr")


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
        return self.status_code is None or _retryable_status(self.status_code)


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


def _retryable_status(status_code: object) -> bool:
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return False
    return code in {408, 409, 425, 429} or code >= 500


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
        _retryable_status(code)
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


def _add_usage(
    total: dict[str, int | None],
    current: dict[str, int | None],
) -> None:
    for name, value in current.items():
        if value is not None:
            total[name] = int(total.get(name) or 0) + value


def _retry_delay(attempt: int, exc: BaseException | None = None) -> float:
    retry_after = getattr(exc, "retry_after_sec", None)
    if isinstance(retry_after, (int, float)) and retry_after >= 0:
        return min(float(retry_after), 30.0)
    return RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))


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
    endpoint_order: tuple[str, ...] = (),
) -> ExtractionResult:
    started = time.monotonic()
    prompt = build_system_prompt(schema_config, instruction_path, effect_definition)
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
    if reasoning_effort is not None:
        payload["reasoning"] = {"effort": normalize_reasoning_effort(reasoning_effort)}
    if input_mode in {"native_pdf", "cloudflare_pdf"}:
        engine = "native" if input_mode == "native_pdf" else OPENROUTER_PDF_ENGINES[0]
        payload["plugins"] = [{"id": "file-parser", "pdf": {"engine": engine}}]
        if input_mode == "cloudflare_pdf":
            payload["max_tokens"] = LOCAL_OUTPUT_TOKEN_RESERVE
    else:
        raise RuntimeError(f"Unsupported OpenRouter input mode: {input_mode}")
    usage: dict[str, int | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    raw_text = ""
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
                time.sleep(_retry_delay(attempt, exc))
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
                time.sleep(_retry_delay(attempt))
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
                time.sleep(_retry_delay(attempt))
                continue
            raise last_error
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            last_error = RuntimeError(f"OpenRouter returned invalid JSON: {exc}")
            last_error.provider_responses = provider_responses
            if should_retry(attempt):
                time.sleep(_retry_delay(attempt))
                continue
            raise last_error from exc

        normalize_extracted_json(parsed, schema_config)
        try:
            validate_extracted_json(parsed, schema_config, provider_name="OpenRouter")
        except ValueError as exc:
            last_error = exc
            last_error.provider_responses = provider_responses
            if should_retry(attempt):
                time.sleep(_retry_delay(attempt))
                continue
            raise
        break
    else:  # pragma: no cover - every unsuccessful branch raises on its last attempt
        raise last_error or RuntimeError("OpenRouter did not complete the extraction.")

    if not isinstance(parsed, dict):  # pragma: no cover - validation guarantees this
        raise RuntimeError("OpenRouter returned JSON that is not a top-level object.")
    return ExtractionResult(
        source_path=source_path,
        source_name=source_name,
        status="ok",
        provider_used="openrouter",
        model_used=str(response.get("model") or primary_model),
        data=parsed,
        raw_json=raw_text,
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
