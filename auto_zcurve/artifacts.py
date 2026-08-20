from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .llm import ExtractionResult


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


def provider_response_path(project_dir: Path, source_name: str) -> Path:
    return raw_dir(project_dir) / f"{source_key(source_name)}.provider-response.json"


def raw_response_path(project_dir: Path, source_name: str) -> Path:
    return raw_dir(project_dir) / f"{source_key(source_name)}.response.txt"


def repaired_response_path(project_dir: Path, source_name: str) -> Path:
    return raw_dir(project_dir) / f"{source_key(source_name)}.response.repaired.txt"


def extraction_record(result: ExtractionResult) -> dict[str, Any]:
    return {
        "source_file": str(result.source_path),
        "source_name": result.source_name,
        "file_name": result.source_name,
        "status": result.status,
        "error": result.error,
        "provider_used": result.provider_used,
        "model_used": result.model_used,
        "effects": result.effect_count,
        "data": result.data,
        "raw_json": result.raw_json,
        "duration_sec": result.duration_sec,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "total_tokens": result.total_tokens,
        "input_mode": result.input_mode,
        "parser_name": result.parser_name,
        "parser_version": result.parser_version,
        "parser_config_version": result.parser_config_version,
        "source_pdf_sha256": result.source_pdf_sha256,
        "parsed_document_sha256": result.parsed_document_sha256,
        "parser_page_count": result.parser_page_count,
        "parser_mean_grade": result.parser_mean_grade,
        "parser_low_grade": result.parser_low_grade,
        "parser_warnings": list(result.parser_warnings),
        "parser_cache_path": result.parser_cache_path,
        "parser_duration_sec": result.parser_duration_sec,
        "estimated_input_tokens": result.estimated_input_tokens,
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
    ensure_output_dirs(project_dir)
    records = load_extractions(project_dir)
    record = extraction_record(result)
    if result.provider_responses:
        response_path = provider_response_path(project_dir, result.source_name)
        record["provider_response_path"] = str(response_path.relative_to(project_dir))
        with response_path.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "source_name": result.source_name,
                    "provider": result.provider_used,
                    "model": result.model_used,
                    "responses": result.provider_responses,
                },
                handle,
                indent=2,
                ensure_ascii=False,
            )
            handle.write("\n")
    if result.raw_response is not None:
        response_path = raw_response_path(project_dir, result.source_name)
        record["raw_response_path"] = str(response_path.relative_to(project_dir))
        response_path.write_text(result.raw_response, encoding="utf-8")
    if result.repaired_response is not None:
        repaired_path = repaired_response_path(project_dir, result.source_name)
        record["repaired_response_path"] = str(repaired_path.relative_to(project_dir))
        repaired_path.write_text(result.repaired_response, encoding="utf-8")
        record["json_repaired"] = True
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


def delete_extraction(project_dir: Path, source_name: str) -> bool:
    """Delete one PDF's extraction record and generated response artifacts."""
    records = load_extractions(project_dir)
    matching = next((item for item in records if item.get("source_name") == source_name), None)
    if matching is None:
        return False
    records = [item for item in records if item.get("source_name") != source_name]
    save_extractions(project_dir, records)
    candidates = {
        raw_path(project_dir, source_name),
        provider_response_path(project_dir, source_name),
        raw_response_path(project_dir, source_name),
        repaired_response_path(project_dir, source_name),
    }
    for key in ("provider_response_path", "raw_response_path", "repaired_response_path"):
        value = str(matching.get(key) or "").strip()
        if value:
            candidate = (project_dir / value).resolve()
            try:
                candidate.relative_to(project_dir.resolve())
            except ValueError:
                continue
            candidates.add(candidate)
    for candidate in candidates:
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    for name in (
        "report.html",
        "zcurve_summary.txt",
        "zcurve_plot.png",
        "zcurve_reproduction_settings.csv",
        "disclosure_table.csv",
    ):
        try:
            (output_dir(project_dir) / name).unlink()
        except (FileNotFoundError, OSError):
            pass
    return True


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
        "provider",
        "model",
        "provider_used",
        "model_used",
        "retry",
        "error",
        "started_at",
        "finished_at",
        "duration_sec",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "input_mode",
        "parser_name",
        "parser_version",
        "parser_config_version",
        "source_pdf_sha256",
        "parsed_document_sha256",
        "parser_page_count",
        "parser_mean_grade",
        "parser_low_grade",
        "parser_warnings",
        "parser_cache_path",
        "parser_duration_sec",
        "estimated_input_tokens",
    ]
    with run_log_csv_path(project_dir).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            csv_row = dict(row)
            warnings = csv_row.get("parser_warnings")
            if isinstance(warnings, (list, tuple)):
                csv_row["parser_warnings"] = " | ".join(str(item) for item in warnings)
            writer.writerow(csv_row)


def latest_by_source(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(record.get("source_name")): record for record in records if record.get("source_name")}


def read_disclosure_summary(project_dir: Path) -> tuple[int, int]:
    path = output_dir(project_dir) / "disclosure_table.csv"
    if not path.exists():
        return 0, 0
    with path.open("r", newline="", encoding="utf-8") as handle:
        previous_limit = csv.field_size_limit()
        try:
            csv.field_size_limit(max(previous_limit, 16 * 1024 * 1024))
            rows = list(csv.DictReader(handle))
        finally:
            csv.field_size_limit(previous_limit)
    usable = sum(str(row.get("usable_for_zcurve", "")).strip().lower() in {"true", "t", "1"} for row in rows)
    return len(rows), usable


def read_zcurve_summary(project_dir: Path) -> str | None:
    path = output_dir(project_dir) / "zcurve_summary.txt"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def parse_zcurve_summary(text: str | None) -> dict[str, Any]:
    if not text:
        return {"execution": None, "metrics": []}

    execution = next(
        (line.strip() for line in text.splitlines() if line.strip().startswith("Bootstrap execution:")),
        None,
    )
    metrics = []
    labels = {
        "ERR": "Expected replication rate",
        "EDR": "Expected discovery rate",
    }
    number = r"(-?(?:\d+(?:\.\d*)?|\.\d+))"
    for code, label in labels.items():
        match = re.search(
            rf"^\s*{re.escape(code)}\s+{number}\s+{number}\s+{number}\s*$",
            text,
            flags=re.MULTILINE,
        )
        if match:
            metrics.append(
                {
                    "code": code,
                    "label": label,
                    "estimate": match.group(1),
                    "lower_ci": match.group(2),
                    "upper_ci": match.group(3),
                }
            )

    odr = re.search(
        rf"ODR\s*=\s*{number}.*?95% CI\s*\[\s*{number}\s*,\s*{number}\s*\]",
        text,
        flags=re.IGNORECASE,
    )
    if odr:
        metrics.append(
            {
                "code": "ODR",
                "label": "Observed discovery rate",
                "estimate": odr.group(1),
                "lower_ci": odr.group(2),
                "upper_ci": odr.group(3),
            }
        )

    return {"execution": execution, "metrics": metrics}
