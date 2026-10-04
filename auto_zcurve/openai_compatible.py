"""OpenAI-compatible Chat Completions with locally extracted PDF text."""
from __future__ import annotations
import json
import time
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MAX_RETRIES = 3
RETRY_BACKOFF_BASE_SEC = 2.0
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
DEFAULT_TIMEOUT_SEC = 600

class ProviderError(RuntimeError):
    def __init__(self, message, *, raw_response=None):
        super().__init__(message)
        self.raw_response = raw_response

class ExtractionCancelled(ProviderError):
    pass

def normalize_base_url(value: str) -> str:
    from urllib.parse import urlsplit

    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment)
        parsed.port
    except ValueError:
        valid = False
    if not valid or any(c.isspace() for c in value):
        raise ValueError("Enter an HTTP(S) API base URL without credentials, query, or fragment.")
    if parsed.path.endswith("/chat/completions"):
        raise ValueError("Enter the API base URL (for example /v1), without /chat/completions.")
    return value


def check_model(model: str, *, api_key: str = "", base_url: str = "") -> list[str]:
    # Model catalog endpoints are optional; never assume OpenRouter metadata.
    try:
        normalize_base_url(base_url)
    except ValueError as exc:
        return [str(exc)]
    return [] if model.strip() else ["Enter the model ID served by your endpoint."]


def pdf_text(path: Path, cancel_event: threading.Event | None) -> str:
    from pypdf import PdfReader

    pages = []
    try:
        for number, page in enumerate(PdfReader(path).pages, 1):
            if cancel_event and cancel_event.is_set():
                raise ExtractionCancelled("Extraction cancelled by user.")
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"[Page {number}]\n{text}")
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"Could not extract PDF text: {exc}") from exc
    if not pages:
        raise ProviderError("PDF has no extractable text. Run OCR first or use a provider with native PDF input.")
    return "\n\n".join(pages)


def _redact(text: str, api_key: str) -> str:
    return text.replace(api_key, "[redacted]") if api_key else text


def _request(url: str, *, api_key: str, body: dict[str, Any] | None = None, method: str = "GET"):
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    return urllib.request.Request(url, data=data, headers=headers, method=method)


def _call(
    *,
    model: str,
    api_key: str,
    prompt: str,
    base_url: str,
    response_format: str = "json_schema",
    response_schema: dict[str, Any],
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    reasoning_effort: str = "",
    cancel_event: threading.Event | None = None,
) -> tuple[str, dict[str, int | None]]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt + "\n\nReturn only JSON matching this schema:\n" + json.dumps(response_schema),
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "auto_zcurve_extraction", "strict": True, "schema": response_schema},
        },
    }
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort

    if response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}
    elif response_format == "none":
        payload.pop("response_format")
    elif response_format != "json_schema":
        raise ProviderError("Unknown JSON output mode.")
    try:
        base_url = normalize_base_url(base_url)
    except ValueError as exc:
        raise ProviderError(str(exc)) from exc
    if not model.strip():
        raise ProviderError("Enter the model ID served by your OpenAI-compatible endpoint.")
    cancel_event = cancel_event or threading.Event()
    last_error: ProviderError | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        if cancel_event.is_set():
            raise ExtractionCancelled("Extraction cancelled by user.")
        request = _request(
            f"{base_url}/chat/completions", api_key=api_key, body=payload, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                raw_body = response.read()
            body = json.loads(raw_body.decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if cancel_event and cancel_event.is_set():
                raise ExtractionCancelled("Extraction cancelled by user.") from exc
            detail = _redact(exc.read().decode("utf-8", errors="replace"), api_key)
            last_error = ProviderError(f"OpenAI-compatible endpoint API error ({exc.code}): {detail}")
            if exc.code not in RETRYABLE_STATUS or attempt == MAX_RETRIES:
                raise last_error from exc
        except (ValueError, UnicodeError) as exc:
            raise ProviderError("Endpoint returned invalid JSON.") from exc
        except (urllib.error.URLError, OSError) as exc:
            if cancel_event and cancel_event.is_set():
                raise ExtractionCancelled("Extraction cancelled by user.") from exc
            last_error = ProviderError(f"Could not reach OpenAI-compatible endpoint: {_redact(str(exc), api_key)}")
            if attempt == MAX_RETRIES or (cancel_event and cancel_event.is_set()):
                raise last_error from exc
        if cancel_event and cancel_event.wait(RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1))):
            raise ExtractionCancelled("Extraction cancelled by user.")
    else:  # pragma: no cover - loop always breaks or raises above
        raise last_error or ProviderError("OpenAI-compatible endpoint request failed for an unknown reason.")

    raw_response = json.dumps(body)
    if not isinstance(body, dict):
        raise ProviderError("OpenAI-compatible endpoint returned an invalid response object.", raw_response=raw_response)
    choices = body.get("choices") or []
    if not isinstance(choices, list) or not choices:
        raise ProviderError("OpenAI-compatible endpoint returned no choices.", raw_response=raw_response)
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ProviderError("OpenAI-compatible endpoint returned an invalid choice.", raw_response=raw_response)
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        raise ProviderError("OpenAI-compatible endpoint returned an invalid message.", raw_response=raw_response)
    text = message.get("content")
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("OpenAI-compatible endpoint returned no message content.", raw_response=raw_response)

    usage = body.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    tokens = {
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
    }
    return text, tokens



def extract_pdf(*, source_path, source_name, api_key, primary_model,
                response_schema, schema_config, instruction_path,
                request_timeout_sec=600, reasoning_effort=None, **kwargs):
    from .config import load_app_settings
    from .llm import ExtractionResult
    from .openrouter import strict_response_schema
    from .prompts import build_system_prompt
    from .schema import normalize_extracted_json, validate_extracted_json

    started = time.monotonic()
    settings = load_app_settings()
    text, tokens = _call(
        model=primary_model, api_key=api_key or "",
        prompt=build_system_prompt(schema_config, instruction_path)
            + "\n\nArticle text:\n" + pdf_text(source_path, None),
        base_url=settings.openai_base_url,
        response_format=settings.openai_response_format,
        response_schema=strict_response_schema(response_schema),
        timeout_sec=request_timeout_sec,
    )
    parsed = json.loads(text)
    normalize_extracted_json(parsed, schema_config)
    validate_extracted_json(parsed, schema_config, provider_name="OpenAI-compatible")
    return ExtractionResult(
        source_path=source_path, source_name=source_name, status="ok",
        provider_used="openai_compatible", model_used=primary_model,
        data=parsed, raw_json=text, duration_sec=round(time.monotonic() - started, 3),
        input_tokens=tokens.get("input_tokens"), output_tokens=tokens.get("output_tokens"),
        total_tokens=sum(v for v in tokens.values() if isinstance(v, int)) or None,
        input_mode="local_text",
    )
