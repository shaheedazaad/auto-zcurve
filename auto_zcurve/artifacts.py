from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .gemini import ExtractionResult


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def output_dir(project_dir: Path) -> Path:
    return project_dir / "output"


def raw_dir(project_dir: Path) -> Path:
    return output_dir(project_dir) / "raw"


def ensure_output_dirs(project_dir: Path) -> None:
    raw_dir(project_dir).mkdir(parents=True, exist_ok=True)


def source_key(source_name: str) -> str:
    stem = Path(source_name).stem
    digest = hashlib.sha1(source_name.encode("utf-8")).hexdigest()[:8]
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in stem)
    return f"{safe}-{digest}"


def raw_path(project_dir: Path, source_name: str) -> Path:
    return raw_dir(project_dir) / f"{source_key(source_name)}.json"


def extraction_record(result: ExtractionResult) -> dict[str, Any]:
    return {
        "source_file": str(result.source_path),
        "source_name": result.source_name,
        "file_name": result.source_name,
        "status": result.status,
        "error": result.error,
        "model_used": result.model_used,
        "effects": result.effect_count,
        "data": result.data,
        "raw_json": result.raw_json,
        "duration_sec": result.duration_sec,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "total_tokens": result.total_tokens,
    }


def load_extractions(project_dir: Path) -> list[dict[str, Any]]:
    path = output_dir(project_dir) / "extractions.json"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if isinstance(loaded, list):
        return loaded
    raise ValueError(f"Expected a JSON array in {path}")


def save_extractions(project_dir: Path, records: list[dict[str, Any]]) -> None:
    ensure_output_dirs(project_dir)
    path = output_dir(project_dir) / "extractions.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def upsert_extraction(project_dir: Path, result: ExtractionResult) -> list[dict[str, Any]]:
    records = load_extractions(project_dir)
    record = extraction_record(result)
    replaced = False
    for index, existing in enumerate(records):
        if existing.get("source_name") == result.source_name:
            records[index] = record
            replaced = True
            break
    if not replaced:
        records.append(record)

    save_extractions(project_dir, records)
    with raw_path(project_dir, result.source_name).open("w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return records


def run_log_json_path(project_dir: Path) -> Path:
    return output_dir(project_dir) / "run_log.json"


def run_log_csv_path(project_dir: Path) -> Path:
    return output_dir(project_dir) / "run_log.csv"


def load_run_log(project_dir: Path) -> list[dict[str, Any]]:
    path = run_log_json_path(project_dir)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    return loaded if isinstance(loaded, list) else []


def append_run_log(project_dir: Path, row: dict[str, Any]) -> list[dict[str, Any]]:
    ensure_output_dirs(project_dir)
    rows = load_run_log(project_dir)
    rows.append(row)
    with run_log_json_path(project_dir).open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    write_run_log_csv(project_dir, rows)
    return rows


def write_run_log_csv(project_dir: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "run_id",
        "attempt",
        "source_name",
        "source_file",
        "status",
        "effects",
        "model",
        "model_used",
        "retry",
        "error",
        "started_at",
        "finished_at",
        "duration_sec",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    ]
    with run_log_csv_path(project_dir).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def latest_by_source(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(record.get("source_name")): record for record in records if record.get("source_name")}


def read_disclosure_summary(project_dir: Path) -> tuple[int, int]:
    path = output_dir(project_dir) / "disclosure_table.csv"
    if not path.exists():
        return 0, 0
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    usable = sum(str(row.get("usable_for_zcurve", "")).strip().lower() in {"true", "t", "1"} for row in rows)
    return len(rows), usable


def read_zcurve_summary(project_dir: Path) -> str | None:
    path = output_dir(project_dir) / "zcurve_summary.txt"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None
