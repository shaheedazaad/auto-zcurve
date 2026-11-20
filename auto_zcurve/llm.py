from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


STATUS_OK = "ok"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_ERROR = "error"
# Statuses whose extracted values are kept and fed into the z-curve analysis.
# A plain run skips them; only a retry (or --force) re-extracts them.
EXTRACTED_STATUSES = frozenset({STATUS_OK, STATUS_NEEDS_REVIEW})

JSON_REPAIRED_REASON = "JSON was repaired"
NO_EFFECTS_REASON = "no effects extracted"

# Each provider request gets this many attempts in total before it fails.
MAX_PROVIDER_ATTEMPTS = 3
RETRY_BASE_DELAY_SEC = 1.0
RETRY_AFTER_CAP_SEC = 30.0


def retryable_status(status_code: object) -> bool:
    """Whether an HTTP status is worth retrying: timeouts, conflicts, rate limits and server errors."""
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return False
    return code in {408, 409, 425, 429} or code >= 500


def retry_delay(attempt: int, exc: BaseException | None = None) -> float:
    """Seconds to wait after a failed attempt: a capped Retry-After, else exponential backoff."""
    retry_after = getattr(exc, "retry_after_sec", None)
    if isinstance(retry_after, (int, float)) and retry_after >= 0:
        return min(float(retry_after), RETRY_AFTER_CAP_SEC)
    return RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))


def finish_reason_review_reason(finish_reason: str | None, normal: Iterable[str] = ("STOP",)) -> str | None:
    """Describe an abnormal provider finish reason, or None when it is normal or missing."""
    value = str(finish_reason or "").strip()
    if not value or value.upper() in {item.upper() for item in normal}:
        return None
    return f"finish reason: {value}"


def parse_json_with_repair(raw_text: str, provider_name: str) -> tuple[dict[str, Any], str | None]:
    """Parse provider JSON, repairing malformed output while preserving both forms.

    Returns the parsed value and the repaired text, which is None when no repair
    was needed. Errors carry `raw_response` (and `repaired_response`) for review.
    """
    try:
        return json.loads(raw_text), None
    except json.JSONDecodeError as original_exc:
        try:
            from json_repair import repair_json
        except ImportError as exc:  # pragma: no cover - dependency is declared at install time
            error = RuntimeError(f"{provider_name} returned invalid JSON and json-repair is not installed.")
            error.raw_response = raw_text  # type: ignore[attr-defined]
            raise error from exc
        repaired_text: str | None = None
        try:
            repaired_text = repair_json(raw_text)
            parsed = json.loads(repaired_text)
        except (TypeError, ValueError, json.JSONDecodeError) as repair_exc:
            error = RuntimeError(
                f"{provider_name} returned invalid JSON and json-repair could not repair it: {original_exc}"
            )
            error.raw_response = raw_text  # type: ignore[attr-defined]
            error.repaired_response = repaired_text  # type: ignore[attr-defined]
            raise error from repair_exc
        return parsed, repaired_text


def extraction_review_reasons(
    repaired_text: str | None,
    finish_reasons: Iterable[str | None],
    normal: Iterable[str] = ("STOP",),
) -> tuple[str, ...]:
    """List why a validated extraction needs review: a JSON repair, then abnormal finish reasons."""
    normal = tuple(normal)
    reasons = [JSON_REPAIRED_REASON] if repaired_text is not None else []
    for finish_reason in finish_reasons:
        reason = finish_reason_review_reason(finish_reason, normal)
        if reason:
            reasons.append(reason)
    return tuple(reasons)


def review_status(reasons: Iterable[str]) -> str:
    """Return the status for an extraction whose JSON validated."""
    return STATUS_NEEDS_REVIEW if any(reasons) else STATUS_OK


def record_review_reasons(record: dict[str, Any]) -> list[str]:
    """Read review reasons from a saved record; records saved before them have none."""
    reasons = record.get("review_reasons")
    if not isinstance(reasons, (list, tuple)):
        return []
    return [str(item) for item in reasons if str(item).strip()]


@dataclass
class ExtractionResult:
    source_path: Path
    source_name: str
    status: str
    model_used: str | None = None
    data: dict[str, Any] | None = None
    raw_json: str | None = None
    raw_response: str | None = None
    repaired_response: str | None = None
    provider_responses: list[dict[str, Any]] | None = None
    error: str | None = None
    duration_sec: float = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    provider_used: str | None = None
    input_mode: str | None = None
    parser_name: str | None = None
    parser_version: str | None = None
    parser_config_version: str | None = None
    source_format: str | None = None
    # SHA-256 of the source file, whatever its source format.
    source_sha256: str | None = None
    # Kept for PDFs so saved projects and reports that read it still work.
    source_pdf_sha256: str | None = None
    parsed_document_sha256: str | None = None
    parser_page_count: int | None = None
    parser_mean_grade: str | None = None
    parser_low_grade: str | None = None
    parser_warnings: tuple[str, ...] = ()
    parser_cache_path: str | None = None
    parser_duration_sec: float | None = None
    estimated_input_tokens: int | None = None
    review_reasons: tuple[str, ...] = ()
    # The extractor that produced this result.
    extractor_id: str | None = None
    extractor_name: str | None = None

    @property
    def pdf_sha256(self) -> str | None:
        """The value saved as `source_pdf_sha256`: set for PDF sources only."""
        if self.source_format in (None, "pdf") and self.source_pdf_sha256:
            return self.source_pdf_sha256
        return self.source_sha256 if self.source_format == "pdf" else None

    @property
    def effect_count(self) -> int:
        effects = (self.data or {}).get("effects") if self.data else None
        return len(effects) if isinstance(effects, list) else 0


def flag_empty_extraction(result: ExtractionResult) -> None:
    """Mark an extraction with no effects for review: a model can return an
    empty list when it fails, and a real article rarely reports no tests."""
    if result.status in EXTRACTED_STATUSES and result.effect_count == 0:
        result.status = STATUS_NEEDS_REVIEW
        result.review_reasons = (*result.review_reasons, NO_EFFECTS_REASON)
