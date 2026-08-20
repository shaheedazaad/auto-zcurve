from __future__ import annotations

import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, replace
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
from .config import RunSettings, load_run_settings, save_run_settings
from .console import CliConsole
from .llm import ExtractionResult
from .models import ModelOption, resolve_input_mode, validate_model_option
from .paths import DEFAULT_SCHEMA
from .providers import extract_pdf, normalize_provider
from .projects import project_instruction_path
from .report import render_report
from .schema import build_response_schema, read_extraction_schema
from .security import redact_secrets


_ARTIFACT_LOCK = threading.Lock()


class RunCancelled(RuntimeError):
    pass


class _CancellationSignal:
    """Combine user cancellation with an internal stop signal."""

    def __init__(self, *events: threading.Event | None) -> None:
        self._events = tuple(event for event in events if event is not None)

    def is_set(self) -> bool:
        return any(event.is_set() for event in self._events)


class _RequestPacer:
    """Enforce a minimum interval between provider request starts."""

    def __init__(self, delay_sec: int) -> None:
        self.delay_sec = max(0, int(delay_sec))
        self._last_started: float | None = None
        self._lock = threading.Lock()

    def wait(self, cancellation_event: object | None) -> None:
        if self.delay_sec <= 0:
            return
        while True:
            _raise_if_cancelled(cancellation_event)
            with self._lock:
                now = time.monotonic()
                remaining = 0 if self._last_started is None else self.delay_sec - (now - self._last_started)
                if remaining <= 0:
                    self._last_started = now
                    return
            time.sleep(min(remaining, 0.1))


def _raise_if_cancelled(cancellation_event: object | None) -> None:
    if cancellation_event is not None and cancellation_event.is_set():
        raise RunCancelled("Run cancelled.")


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
            "provider": normalize_provider(result.provider_used or "gemini"),
            "model": primary_model,
            "provider_used": result.provider_used,
            "model_used": result.model_used,
            "retry": retry,
            "error": result.error,
            "started_at": started_at,
            "finished_at": utc_now(),
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
    model_option: ModelOption,
    cancellation_event: object | None = None,
    request_pacer: _RequestPacer | None = None,
):
    _raise_if_cancelled(cancellation_event)
    started_at = utc_now()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if source_path.stat().st_size > max_bytes:
        result = ExtractionResult(
            source_path=source_path,
            source_name=source_name(project_dir, source_path),
            status="error",
            provider_used=normalize_provider(settings.provider),
            input_mode=model_option.input_mode,
            error=f"File exceeds max_upload_size_mb ({settings.max_upload_size_mb} MB).",
        )
        _raise_if_cancelled(cancellation_event)
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
        if request_pacer is not None:
            request_pacer.wait(cancellation_event)
        result = extract_pdf(
            provider=settings.provider,
            source_path=source_path,
            source_name=source_name(project_dir, source_path),
            api_key=api_key,
            primary_model=settings.primary_model,
            response_schema=response_schema,
            schema_config=config,
            instruction_path=project_instruction_path(project_dir),
            effect_definition=settings.effect_definition,
            request_timeout_sec=settings.request_timeout_sec,
            input_mode=model_option.input_mode,
            context_length=model_option.context_length,
            project_dir=project_dir,
            reasoning_effort=(settings.reasoning_effort if model_option.supports_reasoning else None),
            service_tier=settings.service_tier,
            endpoint_order=model_option.endpoint_order,
        )
        _raise_if_cancelled(cancellation_event)
    except RunCancelled:
        raise
    except Exception as exc:
        _raise_if_cancelled(cancellation_event)
        diagnostics = getattr(exc, "diagnostics", {})
        result = ExtractionResult(
            source_path=source_path,
            source_name=source_name(project_dir, source_path),
            status="error",
            provider_used=normalize_provider(settings.provider),
            input_mode=model_option.input_mode,
            error=redact_secrets(exc, [api_key]),
            parser_name=str(diagnostics.get("parser_name") or "") or None,
            parser_version=str(diagnostics.get("parser_version") or "") or None,
            parser_config_version=str(diagnostics.get("parser_config_version") or "") or None,
            source_pdf_sha256=str(diagnostics.get("source_sha256") or "") or None,
            parsed_document_sha256=str(diagnostics.get("document_sha256") or "") or None,
            parser_page_count=int(diagnostics["page_count"]) if diagnostics.get("page_count") else None,
            parser_mean_grade=str(diagnostics.get("mean_grade") or "") or None,
            parser_low_grade=str(diagnostics.get("low_grade") or "") or None,
            parser_warnings=tuple(str(item) for item in diagnostics.get("warnings") or []),
            parser_cache_path=str(diagnostics.get("cache_metadata_path") or "") or None,
            parser_duration_sec=float(diagnostics["duration_sec"]) if diagnostics.get("duration_sec") else None,
            provider_responses=getattr(exc, "provider_responses", None),
            raw_response=getattr(exc, "raw_response", None),
            repaired_response=getattr(exc, "repaired_response", None),
        )
    _raise_if_cancelled(cancellation_event)
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


def _process_paths(
    *,
    paths: list[Path],
    workers: int,
    project_dir: Path,
    settings: RunSettings,
    api_key: str,
    retry: bool,
    run_id: str,
    model_option: ModelOption,
    progress,
    cancellation_event: threading.Event | None,
) -> None:
    """Process paths concurrently while letting cancellation return immediately.

    Provider calls are synchronous and cannot be safely killed mid-request. Daemon
    workers let the controlling web job or CLI unwind without waiting for them;
    cancellation checks prevent their late results from being persisted.
    """

    pending: queue.Queue[Path] = queue.Queue()
    completed: queue.Queue[tuple[ExtractionResult | None, BaseException | None]] = queue.Queue()
    internal_stop = threading.Event()
    signal = _CancellationSignal(cancellation_event, internal_stop)
    request_pacer = _RequestPacer(settings.request_delay_sec)
    for path in paths:
        pending.put(path)

    def worker() -> None:
        while not signal.is_set():
            try:
                path = pending.get_nowait()
            except queue.Empty:
                return
            if signal.is_set():
                return
            try:
                result = _process_one(
                    project_dir=project_dir,
                    source_path=path,
                    settings=settings,
                    api_key=api_key,
                    retry=retry,
                    run_id=run_id,
                    model_option=model_option,
                    cancellation_event=signal,
                    request_pacer=request_pacer,
                )
            except BaseException as exc:
                internal_stop.set()
                completed.put((None, exc))
                return
            completed.put((result, None))

    threads = [
        threading.Thread(
            target=worker,
            daemon=True,
            name=f"auto-zcurve-pdf-{index + 1}",
        )
        for index in range(workers)
    ]
    for thread in threads:
        thread.start()

    received = 0
    try:
        while received < len(paths):
            _raise_if_cancelled(cancellation_event)
            try:
                result, error = completed.get(timeout=0.1)
            except queue.Empty:
                continue
            if error is not None:
                raise error
            assert result is not None
            received += 1
            progress.advance(f"{result.status}: {result.source_name}")
    except BaseException:
        internal_stop.set()
        raise


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
    cancellation_event: threading.Event | None = None,
) -> RunSummary | None:
    _raise_if_cancelled(cancellation_event)
    if not ensure_project_layout(project_dir, assume_yes, interactive, console):
        return None

    pdfs = discover_pdfs(project_dir)
    console.info(f"Discovered {len(pdfs)} PDF file(s) in {project_dir / 'sources'}.")
    if not pdfs:
        console.warn("Add PDF files to sources/ before running extraction.")
        return None

    previous_settings = load_run_settings(project_dir)
    provider_or_model_changed = bool(
        previous_settings
        and (
            normalize_provider(previous_settings.provider) != normalize_provider(settings.provider)
            or previous_settings.primary_model != settings.primary_model
            or previous_settings.pdf_parser != settings.pdf_parser
            or previous_settings.reasoning_effort != settings.reasoning_effort
            or previous_settings.service_tier != settings.service_tier
        )
    )
    existing = latest_by_source(load_extractions(project_dir))
    to_process = [
        path
        for path in pdfs
        if force
        or provider_or_model_changed
        or existing.get(source_name(project_dir, path), {}).get("status") != "ok"
    ]
    if provider_or_model_changed:
        console.info("The provider, model, PDF parser, reasoning level, or service tier changed; all PDFs will be reprocessed.")
    skipped = len(pdfs) - len(to_process)
    if skipped:
        console.info(f"Skipping {skipped} PDF(s) with existing successful extractions. Use --force to rerun them.")

    run_id = str(uuid.uuid4())
    model_option = validate_model_option(
        settings.provider,
        settings.primary_model,
        api_key,
        timeout_sec=min(settings.request_timeout_sec, 30),
    )
    _raise_if_cancelled(cancellation_event)
    model_option = replace(
        model_option,
        input_mode=resolve_input_mode(settings.provider, model_option, settings.pdf_parser),
    )
    settings.primary_model = model_option.name
    save_run_settings(project_dir, settings)
    if to_process:
        workers = max(1, min(settings.parallel_requests, len(to_process)))
        worker_label = "worker" if workers == 1 else "workers"
        with console.progress(
            len(to_process),
            f"Extracting PDFs with {workers} parallel {worker_label}",
        ) as progress:
            _process_paths(
                paths=to_process,
                workers=workers,
                project_dir=project_dir,
                settings=settings,
                api_key=api_key,
                retry=False,
                run_id=run_id,
                model_option=model_option,
                progress=progress,
                cancellation_event=cancellation_event,
            )

    report_path = None
    _raise_if_cancelled(cancellation_event)
    if not skip_report:
        report_path = render_report(
            project_dir=project_dir,
            schema_path=project_dir / "extraction_schema.yml",
            model_name=settings.primary_model,
            effect_definition=settings.effect_definition,
            instruction_path=project_instruction_path(project_dir),
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
    cancellation_event: threading.Event | None = None,
) -> RunSummary | None:
    _raise_if_cancelled(cancellation_event)
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
    model_option = validate_model_option(
        settings.provider,
        settings.primary_model,
        api_key,
        timeout_sec=min(settings.request_timeout_sec, 30),
    )
    _raise_if_cancelled(cancellation_event)
    model_option = replace(
        model_option,
        input_mode=resolve_input_mode(settings.provider, model_option, settings.pdf_parser),
    )
    settings.primary_model = model_option.name
    save_run_settings(project_dir, settings)
    run_id = str(uuid.uuid4())
    workers = max(1, min(settings.parallel_requests, len(paths)))
    worker_label = "worker" if workers == 1 else "workers"
    with console.progress(
        len(paths),
        f"Retrying failed PDFs with {workers} parallel {worker_label}",
    ) as progress:
        _process_paths(
            paths=paths,
            workers=workers,
            project_dir=project_dir,
            settings=settings,
            api_key=api_key,
            retry=True,
            run_id=run_id,
            model_option=model_option,
            progress=progress,
            cancellation_event=cancellation_event,
        )

    report_path = None
    _raise_if_cancelled(cancellation_event)
    if not skip_report:
        report_path = render_report(
            project_dir=project_dir,
            schema_path=project_dir / "extraction_schema.yml",
            model_name=settings.primary_model,
            effect_definition=settings.effect_definition,
            instruction_path=project_instruction_path(project_dir),
        )
        try:
            rel = report_path.relative_to(project_dir)
        except ValueError:
            rel = report_path
        console.success(f"R/Quarto report created: {rel}")
    return summarize(project_dir, report_path)


def regenerate_report(
    *,
    project_dir: Path,
    settings: RunSettings,
    console: CliConsole,
    cancellation_event: threading.Event | None = None,
) -> RunSummary:
    if cancellation_event is not None and cancellation_event.is_set():
        raise RunCancelled("Report regeneration cancelled.")

    with console.progress(1, "Rendering report") as progress:
        report_path = render_report(
            project_dir=project_dir,
            schema_path=project_dir / "extraction_schema.yml",
            model_name=settings.primary_model,
            effect_definition=settings.effect_definition,
            instruction_path=project_instruction_path(project_dir),
        )
        progress.advance("Report rendered")

    if cancellation_event is not None and cancellation_event.is_set():
        raise RunCancelled("Report regeneration cancelled.")
    console.success("R/Quarto report regenerated: output/report.html")
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
