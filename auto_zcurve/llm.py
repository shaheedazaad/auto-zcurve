from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


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

    @property
    def effect_count(self) -> int:
        effects = (self.data or {}).get("effects") if self.data else None
        return len(effects) if isinstance(effects, list) else 0
