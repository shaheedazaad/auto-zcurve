from __future__ import annotations

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


def finish_reason_review_reason(finish_reason: str | None, normal: Iterable[str] = ("STOP",)) -> str | None:
    """Describe an abnormal provider finish reason, or None when it is normal or missing."""
    value = str(finish_reason or "").strip()
    if not value or value.upper() in {item.upper() for item in normal}:
        return None
    return f"finish reason: {value}"


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

    @property
    def effect_count(self) -> int:
        effects = (self.data or {}).get("effects") if self.data else None
        return len(effects) if isinstance(effects, list) else 0
