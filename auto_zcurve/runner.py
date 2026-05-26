from __future__ import annotations

import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import (
    append_run_log,
    ensure_output_dirs,
    latest_by_source,
    load_extractions,
    load_run_log,
    read_disclosure_summary,
    upsert_extraction,
    utc_now,
)
from .config import RunSettings, save_run_settings
from .console import CliConsole
from .gemini import ExtractionResult, extract_pdf
from .paths import DEFAULT_INSTRUCTIONS, DEFAULT_SCHEMA
from .report import render_report
from .schema import build_response_schema, read_extraction_schema


_ARTIFACT_LOCK = threading.Lock()


@dataclass
class RunSummary:
    report_path: Path | None
    successful_pdfs: int
    failed_pdfs: int
    extracted_effects: int
    usable_zcurve_inputs: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


def discover_pdfs(project_dir: Path) -> list[Path]:
    sources = project_dir / "sources"
    return sorted(path for path in sources.rglob("*.pdf") if path.is_file())


def source_name(project_dir: Path, source_path: Path) -> str:
    return source_path.relative_to(project_dir / "sources").as_posix()


def ensure_project_layout(project_dir: Path, assume_yes: bool, interactive: bool, console: CliConsole) -> bool:
    project_dir.mkdir(parents=True, exist_ok=True)
    sources = project_dir / "sources"
    if not sources.exists():
        if assume_yes or _confirm("Create the missing sources/ folder?", default=True, interactive=interactive):
            sources.mkdir(parents=True, exist_ok=True)
            console.warn(f"Created {sources}. Add PDF files there, then rerun auto-zcurve.")
            return False
        raise RuntimeError("A project sources/ folder is required.")

    schema_path = project_dir / "extraction_schema.yml"
    if not schema_path.exists():
        if assume_yes or _confirm("Copy the bundled default extraction schema into this project?", default=True, interactive=interactive):
            shutil.copyfile(DEFAULT_SCHEMA, schema_path)
            console.info(f"Copied default schema to {schema_path}.")
            notify_default_schema = getattr(console, "default_schema_created", None)
            if callable(notify_default_schema):
                notify_default_schema(schema_path)
        else:
            raise RuntimeError("A project extraction_schema.yml file is required.")

    ensure_output_dirs(project_dir)
    return True


def _confirm(message: str, default: bool, interactive: bool) -> bool:
    if not interactive:
        return default
    try:
        import questionary

        return bool(questionary.confirm(message, default=default).ask())
    except Exception:
        suffix = "Y/n" if default else "y/N"
        answer = input(f"{message} [{suffix}] ").strip().lower()
        if not answer:
            return default
        return answer in {"y", "yes"}


def _attempt_number(project_dir: Path, source: str) -> int:
    return 1 + sum(row.get("source_name") == source for row in load_run_log(project_dir))


def _log_result(
    *,
    project_dir: Path,
    run_id: str,
    result: ExtractionResult,
    primary_model: str,
    retry: bool,
    started_at: str,
) -> None:
    append_run_log(
        project_dir,
        {
            "run_id": run_id,
            "attempt": _attempt_number(project_dir, result.source_name),
            "source_name": result.source_name,
            "source_file": str(result.source_path),
            "status": result.status,
            "effects": result.effect_count,
            "model": primary_model,
            "model_used": result.model_used,
            "retry": retry,
            "error": result.error,
            "started_at": started_at,
            "finished_at": utc_now(),
            "duration_sec": result.duration_sec,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "total_tokens": result.total_tokens,
        },
    )


def _process_one(
    *,
    project_dir: Path,
    source_path: Path,
    settings: RunSettings,
    api_key: str,
    retry: bool,
    run_id: str,
):
    started_at = utc_now()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if source_path.stat().st_size > max_bytes:
        result = ExtractionResult(
            source_path=source_path,
            source_name=source_name(project_dir, source_path),
            status="error",
            error=f"File exceeds max_upload_size_mb ({settings.max_upload_size_mb} MB).",
        )
        with _ARTIFACT_LOCK:
            upsert_extraction(project_dir, result)
            _log_result(
                project_dir=project_dir,
                run_id=run_id,
                result=result,
                primary_model=settings.primary_model,
                retry=retry,
                started_at=started_at,
            )
        return result

    config = read_extraction_schema(project_dir / "extraction_schema.yml")
    response_schema = build_response_schema(config)
    try:
        result = extract_pdf(
            source_path=source_path,
            source_name=source_name(project_dir, source_path),
            api_key=api_key,
            primary_model=settings.primary_model,
            response_schema=response_schema,
            schema_config=config,
            instruction_path=DEFAULT_INSTRUCTIONS,
            effect_definition=settings.effect_definition,
        )
    except Exception as exc:
        result = ExtractionResult(
            source_path=source_path,
            source_name=source_name(project_dir, source_path),
            status="error",
            error=str(exc),
        )
    with _ARTIFACT_LOCK:
        upsert_extraction(project_dir, result)
        _log_result(
            project_dir=project_dir,
            run_id=run_id,
            result=result,
            primary_model=settings.primary_model,
            retry=retry,
            started_at=started_at,
        )
    return result


def run_project(
    *,
    project_dir: Path,
    settings: RunSettings,
    assume_yes: bool,
    interactive: bool,
    force: bool,
    skip_report: bool,
    console: CliConsole,
    api_key: str,
) -> RunSummary | None:
    if not ensure_project_layout(project_dir, assume_yes, interactive, console):
        return None

    pdfs = discover_pdfs(project_dir)
    console.info(f"Discovered {len(pdfs)} PDF file(s) in {project_dir / 'sources'}.")
    if not pdfs:
        console.warn("Add PDF files to sources/ before running extraction.")
        return None

    save_run_settings(project_dir, settings)
    existing = latest_by_source(load_extractions(project_dir))
    to_process = [
        path
        for path in pdfs
        if force or existing.get(source_name(project_dir, path), {}).get("status") != "ok"
    ]
    skipped = len(pdfs) - len(to_process)
    if skipped:
        console.info(f"Skipping {skipped} PDF(s) with existing successful extractions. Use --force to rerun them.")

    run_id = str(uuid.uuid4())
    if to_process:
        workers = max(1, min(settings.parallel_requests, len(to_process)))
        with console.progress(len(to_process), "Extracting PDFs") as progress:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(
                        _process_one,
                        project_dir=project_dir,
                        source_path=path,
                        settings=settings,
                        api_key=api_key,
                        retry=False,
                        run_id=run_id,
                    )
                    for path in to_process
                ]
                for future in as_completed(futures):
                    result = future.result()
                    label = f"{result.status}: {result.source_name}"
                    progress.advance(label)

    report_path = None
    if not skip_report:
        report_path = render_report(
            project_dir=project_dir,
            schema_path=project_dir / "extraction_schema.yml",
            model_name=settings.primary_model,
            effect_definition=settings.effect_definition,
        )
        try:
            rel = report_path.relative_to(project_dir)
        except ValueError:
            rel = report_path
        console.success(f"R/Quarto report created: {rel}")

    return summarize(project_dir, report_path)


def retry_project(
    *,
    project_dir: Path,
    settings: RunSettings,
    selected_sources: list[str] | None,
    assume_yes: bool,
    skip_report: bool,
    console: CliConsole,
    api_key: str,
) -> RunSummary | None:
    if not ensure_project_layout(project_dir, assume_yes, interactive=False, console=console):
        return None
    latest = latest_by_source(load_extractions(project_dir))
    failures = [name for name, record in latest.items() if record.get("status") != "ok"]
    if selected_sources:
        failures = [name for name in failures if name in set(selected_sources)]
    if not failures:
        console.success("No failed PDFs to retry.")
        existing_report = project_dir / "output" / "report.html"
        return summarize(project_dir, existing_report if existing_report.exists() else None)

    paths = [project_dir / "sources" / name for name in failures]
    save_run_settings(project_dir, settings)
    run_id = str(uuid.uuid4())
    with console.progress(len(paths), "Retrying failed PDFs") as progress:
        with ThreadPoolExecutor(max_workers=max(1, min(settings.parallel_requests, len(paths)))) as executor:
            futures = [
                executor.submit(
                    _process_one,
                    project_dir=project_dir,
                    source_path=path,
                    settings=settings,
                    api_key=api_key,
                    retry=True,
                    run_id=run_id,
                )
                for path in paths
            ]
            for future in as_completed(futures):
                result = future.result()
                progress.advance(f"{result.status}: {result.source_name}")

    report_path = None
    if not skip_report:
        report_path = render_report(
            project_dir=project_dir,
            schema_path=project_dir / "extraction_schema.yml",
            model_name=settings.primary_model,
            effect_definition=settings.effect_definition,
        )
        try:
            rel = report_path.relative_to(project_dir)
        except ValueError:
            rel = report_path
        console.success(f"R/Quarto report created: {rel}")
    return summarize(project_dir, report_path)


def summarize(project_dir: Path, report_path: Path | None) -> RunSummary:
    records = load_extractions(project_dir)
    successful = sum(record.get("status") == "ok" for record in records)
    failed = sum(record.get("status") != "ok" for record in records)
    effects = sum(int(record.get("effects") or 0) for record in records if record.get("status") == "ok")
    disclosure_rows, usable = read_disclosure_summary(project_dir)
    input_tokens = sum(int(record.get("input_tokens") or 0) for record in records)
    output_tokens = sum(int(record.get("output_tokens") or 0) for record in records)
    total_tokens = sum(int(record.get("total_tokens") or 0) for record in records)
    return RunSummary(
        report_path=report_path,
        successful_pdfs=successful,
        failed_pdfs=failed,
        extracted_effects=effects or disclosure_rows,
        usable_zcurve_inputs=usable,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )
