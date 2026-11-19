from __future__ import annotations

import asyncio
import io
import json
import os
import platform
import secrets
import socket
import subprocess
import threading
import urllib.request
import webbrowser
import zipfile
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__
from .artifacts import delete_extraction, latest_by_source, load_extractions
from .config import (
    DEFAULTS,
    PDF_PARSER_LABELS,
    REASONING_EFFORT_LABELS,
    SERVICE_TIER_LABELS,
    AppSettings,
    RunSettings,
    load_app_settings,
    load_run_settings,
    normalize_pdf_parser,
    save_app_settings,
    save_selected_extractor_id,
)
from .credentials import KeyVault, VaultUnavailable
from .extractors import (
    RESPONSE_FORMAT_LABELS,
    Extractor,
    ExtractorError,
    check_model,
    create_extractor,
    delete_extractor,
    duplicate_extractor,
    get_extractor,
    legacy_claims,
    load_extractors,
    project_extractor,
    record_legacy_adoption,
    run_settings_for,
    set_key_saved,
    update_extractor,
)
from .openrouter import list_model_endpoints
from .models import list_live_models, normalize_model_name
from .paths import DEFAULT_INSTRUCTIONS
from .preflight import run_preflight
from .prompts import build_system_prompt
from .projects import (
    AnalysisResetRequired,
    ManagedProject,
    ProjectError,
    UploadTooLarge,
    create_project,
    delete_project,
    get_project,
    list_projects,
    project_instruction_path,
    project_snapshot,
    read_project_schema,
    rename_project,
    reset_project_analysis,
    save_upload,
    update_project_instructions,
    update_project_schema,
)
from .runner import RunCancelled, regenerate_report, retry_project, run_project
from .schema import read_extraction_schema
from .security import redact_secrets
from .sources import discover_sources, is_source_name, source_format
from .spreadsheet import SPREADSHEET_SAFE_FILES, spreadsheet_safe_csv, spreadsheet_safe_name
from .user_facing import classify_error
from .providers import PROVIDERS, provider_label


PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
STATIC_DIR = PACKAGE_DIR / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MAX_UPLOAD_REQUEST_BYTES = 512 * 1024 * 1024

# Explicit rather than mimetypes.guess_type() (FileResponse's default): that
# consults the OS's MIME database, which resolves .js to application/javascript
# on Windows and text/javascript on macOS/Linux -- inconsistent behavior for a
# fixed, small set of files we already know the type of.
_STATIC_MEDIA_TYPES = {
    ".css": "text/css",
    ".js": "text/javascript",
    ".txt": "text/plain",
}


def _static_media_type(filename: str) -> str | None:
    return _STATIC_MEDIA_TYPES.get(Path(filename).suffix)


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        authority = request.headers.get("host", "").lower()
        if authority.startswith("["):
            closing_bracket = authority.find("]")
            host = authority[: closing_bracket + 1] if closing_bracket >= 0 else ""
            suffix = authority[closing_bracket + 1 :] if closing_bracket >= 0 else "invalid"
            valid_port = not suffix or (
                suffix.startswith(":") and suffix[1:].isdigit()
            )
            if not valid_port:
                host = ""
        else:
            host, separator, port = authority.partition(":")
            if separator and (":" in port or not port.isdigit()):
                host = ""
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "Invalid Host header."}, status_code=400)

        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            content_length = request.headers.get("content-length", "")
            if content_length.isdigit() and int(content_length) > MAX_UPLOAD_REQUEST_BYTES:
                return JSONResponse({"detail": "Upload request is too large."}, status_code=413)
            origin = request.headers.get("origin")
            if origin:
                expected_http = f"http://{request.headers.get('host')}"
                expected_https = f"https://{request.headers.get('host')}"
                if origin.rstrip("/") not in {expected_http, expected_https}:
                    return JSONResponse({"detail": "Cross-origin request rejected."}, status_code=403)
            if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
                return JSONResponse({"detail": "Cross-site request rejected."}, status_code=403)
        return await call_next(request)


class Job:
    def __init__(self, project_id: str, action: str) -> None:
        self.project_id = project_id
        self.action = action
        self.status = "queued"
        self.events: list[dict] = []
        self.summary: dict | None = None
        self.error: dict | None = None
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        label = "Report regeneration" if action == "report" else action.title()
        self.emit("status", message=f"{label} queued", status="queued")

    def emit(self, event: str, **payload) -> None:
        with self._lock:
            self.events.append({"event": event, **payload})

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "project_id": self.project_id,
                "action": self.action,
                "status": self.status,
                "summary": self.summary,
                "error": self.error,
                "events": list(self.events),
            }


class WebConsole:
    def __init__(self, job: Job, secrets: list[str] | None = None) -> None:
        self.job = job
        self.secrets = list(secrets or [])

    def print(self, *parts: object) -> None:
        self.job.emit("log", message=redact_secrets(" ".join(str(part) for part in parts), self.secrets))

    def title(self, text: str, subtitle: str | None = None) -> None:
        self.job.emit(
            "log",
            message=redact_secrets(text if subtitle is None else f"{text} — {subtitle}", self.secrets),
        )

    def info(self, text: str) -> None:
        self.job.emit("log", level="info", message=redact_secrets(text, self.secrets))

    def warn(self, text: str) -> None:
        self.job.emit("log", level="warning", message=redact_secrets(text, self.secrets))

    def error(self, text: str) -> None:
        self.job.emit("log", level="error", message=redact_secrets(text, self.secrets))

    def success(self, text: str) -> None:
        self.job.emit("log", level="success", message=redact_secrets(text, self.secrets))

    def table(self, title: str, columns, rows) -> None:
        self.job.emit(
            "table",
            title=title,
            columns=list(columns),
            rows=[
                [redact_secrets(value, self.secrets) for value in row]
                for row in rows
            ],
        )

    @contextmanager
    def progress(self, total: int, description: str) -> Iterator["WebProgress"]:
        self.job.emit("progress", total=total, completed=0, message=description)
        yield WebProgress(self.job, total)


class WebProgress:
    def __init__(self, job: Job, total: int) -> None:
        self.job = job
        self.total = total
        self.completed = 0

    def advance(self, description: str | None = None) -> None:
        self.completed += 1
        self.job.emit(
            "progress",
            total=self.total,
            completed=self.completed,
            message=description or "Processed",
        )


class WebRuntime:
    def __init__(self, token: str, projects_root: Path | None = None) -> None:
        self.token = token
        self.projects_root = projects_root
        # Read from the credential store at most once, the first time a key is needed.
        self.vault = KeyVault(legacy_claims=legacy_claims, on_legacy_adopted=record_legacy_adoption)
        self.jobs: dict[str, Job] = {}
        self.update_version: str | None = None
        self._jobs_lock = threading.Lock()

    def project(self, project_id: str) -> ManagedProject:
        return get_project(project_id, root=self.projects_root)

    def has_key(self, extractor: Extractor) -> bool:
        """Whether a key is saved or held for this session, without opening the credential store."""
        return extractor.key_saved or bool(extractor.legacy_credential) or self.vault.cached(extractor.id) is not None

    def key_for(self, extractor: Extractor) -> str | None:
        """Blocking: may open the credential store once. Call via a threadpool from async routes."""
        cached = self.vault.cached(extractor.id)
        if cached is not None:
            return cached
        if not (extractor.key_saved or extractor.legacy_credential):
            return None
        return self.vault.get(extractor.id)

    def secrets(self) -> list[str]:
        return self.vault.secrets()

    def check_for_update(self) -> None:
        request = urllib.request.Request(
            "https://api.github.com/repos/shaheedazaad/auto-zcurve/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"auto-zcurve/{__version__}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.load(response)
            latest = str(payload.get("tag_name") or "").lstrip("v")
            current = tuple(int(part) for part in __version__.split("."))
            candidate = tuple(int(part) for part in latest.split("."))
            if candidate > current:
                self.update_version = latest
        except Exception:
            return

    def start_job(self, project: ManagedProject, action: str, settings: RunSettings, api_key: str | None = None) -> Job:
        with self._jobs_lock:
            existing = self.jobs.get(project.project_id)
            if existing and existing.status in {"queued", "running"}:
                raise ProjectError("This project is already running.")
            job = Job(project.project_id, action)
            self.jobs[project.project_id] = job
        thread = threading.Thread(
            target=self._run_job,
            args=(job, project, action, settings, api_key),
            daemon=True,
            name=f"auto-zcurve-{project.project_id}",
        )
        thread.start()
        return job

    def _run_job(self, job: Job, project: ManagedProject, action: str, settings: RunSettings, api_key: str | None) -> None:
        job.status = "running"
        label = "Report regeneration" if action == "report" else action.title()
        job.emit("status", status="running", message=f"{label} started")
        try:
            console = WebConsole(job, self.secrets())
            run_preflight(project.path, interactive=False, console=console)
            if action == "report":
                summary = regenerate_report(
                    project_dir=project.path,
                    settings=settings,
                    console=console,
                    cancellation_event=job.cancel_event,
                )
            elif action == "retry":
                summary = retry_project(
                    project_dir=project.path,
                    settings=settings,
                    selected_sources=None,
                    assume_yes=True,
                    skip_report=False,
                    console=console,
                    api_key=api_key or "",
                    cancellation_event=job.cancel_event,
                )
            else:
                summary = run_project(
                    project_dir=project.path,
                    settings=settings,
                    assume_yes=True,
                    interactive=False,
                    force=False,
                    skip_report=False,
                    console=console,
                    api_key=api_key or "",
                    cancellation_event=job.cancel_event,
                )
            if job.cancel_event.is_set():
                raise RunCancelled("Run cancelled.")
            job.summary = asdict(summary) if summary else None
            if job.summary and job.summary.get("report_path"):
                job.summary["report_path"] = str(job.summary["report_path"])
            job.status = "complete"
            job.emit("status", status="complete", message="Results are ready")
        except RunCancelled:
            job.status = "cancelled"
            job.emit("status", status="cancelled", message="Run cancelled")
        except Exception as exc:
            friendly = classify_error(exc)
            job.error = {
                key: redact_secrets(value, self.secrets())
                for key, value in asdict(friendly).items()
                if value is not None
            }
            job.status = "failed"
            job.emit(
                "status",
                status="failed",
                message=redact_secrets(friendly.compact(), self.secrets()),
            )


def _extractor_view(runtime: WebRuntime, extractor: Extractor) -> dict[str, object]:
    """What the pages need to show an extractor. Never includes the key."""
    return {
        "id": extractor.id,
        "name": extractor.name,
        "provider": extractor.provider,
        "provider_label": extractor.provider_label,
        "model": extractor.model,
        "summary": extractor.summary(),
        "settings": extractor.snapshot(),
        "has_key": runtime.has_key(extractor),
        # False for a key held in memory for this session only.
        "key_saved": extractor.key_saved or bool(extractor.legacy_credential),
        "requires_key": extractor.requires_key,
    }


def _project_context(request: Request, runtime: WebRuntime, project: ManagedProject) -> dict:
    app_settings = load_app_settings()
    snapshot = project_snapshot(project)
    snapshot["pdf_parser"] = app_settings.pdf_parser
    snapshot["pdf_parser_label"] = PDF_PARSER_LABELS[app_settings.pdf_parser]
    snapshot["max_upload_size_mb"] = app_settings.max_upload_size_mb
    chosen = project_extractor(project.path)
    selected = chosen.extractor
    key_ready = selected is not None and (runtime.has_key(selected) or not selected.requires_key)
    model_ready = selected is not None and bool(selected.model)
    job = runtime.jobs.get(project.project_id)
    return {
        "request": request,
        "token": runtime.token,
        "version": __version__,
        "project": snapshot,
        "app_settings": app_settings,
        "job": job.snapshot() if job else None,
        "schema_text": read_project_schema(project),
        "extractors": [_extractor_view(runtime, item) for item in load_extractors()],
        "selected_extractor_id": chosen.extractor.id if chosen.extractor else "",
        "selected_extractor_deleted": chosen.deleted,
        "run_ready": key_ready and model_ready,
        "model_missing": selected is not None and not model_ready,
        "key_missing": model_ready and not key_ready,
        "is_macos": platform.system() == "Darwin",
        "update_version": runtime.update_version,
    }


def _extractor_values(form: dict[str, object]) -> dict[str, object]:
    """Extractor settings from a submitted form, without the key."""
    return {name: value for name, value in form.items() if name != "api_key"}


def _project_index_snapshot(project: ManagedProject) -> dict:
    sources = discover_sources(project.path, recursive=False)
    records = latest_by_source(load_extractions(project.path))
    source_records = [records.get(source.name, {}) for source in sources]
    return {
        "id": project.project_id,
        "name": project.name,
        "pdf_count": len(sources),
        "failed_count": sum(record.get("status") == "error" for record in source_records),
        "total_tokens": sum(int(record.get("total_tokens") or 0) for record in source_records),
        "has_report": (project.path / "output" / "report.html").is_file(),
    }


def _spreadsheet_safe_download(path: Path) -> bytes:
    """Blocking: read a canonical CSV and build its spreadsheet-safe copy. Call via a worker thread."""
    return spreadsheet_safe_csv(path.read_text(encoding="utf-8"))


def _results_zip(project: ManagedProject) -> bytes:
    """Compress the project's result files into an in-memory results ZIP."""
    output = project.path / "output"
    buffer = io.BytesIO()
    result_names = {
        "report.html",
        "report.qmd",
        "disclosure_table.csv",
        "extractions.json",
        "run_log.csv",
        "run_log.json",
        "zcurve_summary.txt",
        "zcurve_plot.png",
        "zcurve_reproduction_settings.csv",
        "report_render.log",
    }
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        schema_path = project.path / "extraction_schema.yml"
        if schema_path.is_file():
            archive.write(schema_path, "extraction_schema.yml")
            run_settings = load_run_settings(project.path)
            rendered_instructions = build_system_prompt(
                read_extraction_schema(schema_path),
                project_instruction_path(project.path),
            )
            archive.writestr("extraction_instructions.md", rendered_instructions)
        if output.exists():
            for path in sorted(output.rglob("*")):
                relative = path.relative_to(output)
                is_raw_artifact = (
                    len(relative.parts) == 2
                    and relative.parts[0] == "raw"
                    and path.suffix in {".json", ".txt"}
                )
                if path.is_file() and (path.name in result_names or is_raw_artifact):
                    archive.write(path, path.relative_to(project.path).as_posix())
    return buffer.getvalue()


def create_app(*, token: str | None = None, projects_root: Path | None = None) -> FastAPI:
    token = token or secrets.token_urlsafe(32)
    runtime = WebRuntime(token, projects_root=projects_root)
    app = FastAPI(title="auto-zcurve", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime
    app.add_middleware(LocalSecurityMiddleware)

    @app.get("/", include_in_schema=False)
    async def root():
        raise HTTPException(status_code=404)

    @app.get(f"/{token}/static/{{filename}}", include_in_schema=False)
    async def static_file(filename: str):
        if filename not in {
            "app.css",
            "app.js",
        }:
            raise HTTPException(status_code=404)
        return FileResponse(STATIC_DIR / filename, media_type=_static_media_type(filename))

    # Vendored UI assets (Basecoat, MIT). Served locally rather than from a CDN:
    # the session token lives in the URL path, so a third-party script would run
    # with full access to it, and university networks may block a CDN outright.
    @app.get(f"/{token}/static/vendor/{{filename}}", include_in_schema=False)
    async def vendor_file(filename: str):
        if filename not in {
            "basecoat.min.css",
            "basecoat.min.js",
            "basecoat.LICENSE.txt",
        }:
            raise HTTPException(status_code=404)
        return FileResponse(STATIC_DIR / "vendor" / filename, media_type=_static_media_type(filename))

    @app.get(f"/{token}/", response_class=HTMLResponse)
    async def home(request: Request):
        projects = [_project_index_snapshot(item) for item in list_projects(root=projects_root)]
        return TEMPLATES.TemplateResponse(
            request,
            "home.html",
            {
                "token": token,
                "version": __version__,
                "projects": projects,
                "app_settings": load_app_settings(),
                "pdf_parser_label": PDF_PARSER_LABELS[load_app_settings().pdf_parser],
                "update_version": runtime.update_version,
            },
        )

    @app.get(f"/{token}/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "settings.html",
            {
                "token": token,
                "version": __version__,
                "extractors": [_extractor_view(runtime, item) for item in load_extractors()],
                "providers": [(name, provider_label(name)) for name in PROVIDERS],
                "extractor_defaults": Extractor(id="", name="", provider="gemini", model=""),
                "keyring_available": runtime.vault.persistent,
                "is_macos": platform.system() == "Darwin",
                "app_settings": load_app_settings(),
                "pdf_parser_labels": PDF_PARSER_LABELS,
                "reasoning_effort_labels": REASONING_EFFORT_LABELS,
                "service_tier_labels": SERVICE_TIER_LABELS,
                "response_format_labels": RESPONSE_FORMAT_LABELS,
                "default_instructions": DEFAULT_INSTRUCTIONS.read_text(encoding="utf-8"),
                "update_version": runtime.update_version,
            },
        )

    @app.post(f"/{token}/settings")
    async def update_settings(
        pdf_parser: str = Form(DEFAULTS["pdf_parser"]),
        max_upload_size_mb: int = Form(DEFAULTS["max_upload_size_mb"]),
    ):
        try:
            settings = AppSettings(
                pdf_parser=normalize_pdf_parser(pdf_parser),
                max_upload_size_mb=max(1, min(int(max_upload_size_mb), 512)),
            )
            save_app_settings(settings)
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "saved": True,
            "pdf_parser": settings.pdf_parser,
            "pdf_parser_label": PDF_PARSER_LABELS[settings.pdf_parser],
        }

    def _save_key(extractor: Extractor, api_key: str) -> str | None:
        """Blocking: store a key for an extractor. Returns a warning, if any."""
        try:
            persisted = runtime.vault.set(extractor.id, api_key)
        except VaultUnavailable as exc:
            return f"The key was not saved. {exc}"
        set_key_saved(extractor.id, persisted)
        if not persisted:
            return "No secure credential store is available, so the key is kept for this app session only."
        return None

    def _extractor_response(extractor_id: str, warning: str | None = None) -> dict:
        extractor = get_extractor(extractor_id)
        assert extractor is not None
        return {"extractor": _extractor_view(runtime, extractor), "warning": warning}

    @app.post(f"/{token}/extractors")
    async def new_extractor(request: Request):
        form = dict(await request.form())
        api_key = str(form.get("api_key") or "").strip()
        values = _extractor_values(form)

        def create() -> dict:
            created = create_extractor(values, check=lambda item: check_model(item, api_key) if api_key else None)
            warning = _save_key(created, api_key) if api_key else None
            if not api_key and created.provider != "openai_compatible":
                warning = "Saved without a key. Add one before running; the model will be checked when it runs."
            return _extractor_response(created.id, warning)

        try:
            return await asyncio.to_thread(create)
        except (ExtractorError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(f"/{token}/extractors/{{extractor_id}}")
    async def edit_extractor(extractor_id: str, request: Request):
        if get_extractor(extractor_id) is None:
            raise HTTPException(status_code=404, detail="That extractor no longer exists.")
        values = _extractor_values(dict(await request.form()))
        warnings: list[str] = []

        def check(current: Extractor, updated: Extractor) -> None:
            if updated.model == current.model or updated.provider == "openai_compatible":
                return
            try:
                key = runtime.key_for(current)
            except VaultUnavailable:
                key = None
            if key:
                check_model(updated, key)
            else:
                warnings.append("The model will be checked when it runs.")

        try:
            updated = await asyncio.to_thread(update_extractor, extractor_id, values, check=check)
        except (ExtractorError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _extractor_response(updated.id, " ".join(warnings) or None)

    @app.post(f"/{token}/extractors/{{extractor_id}}/duplicate")
    async def copy_extractor(extractor_id: str):
        try:
            copy = duplicate_extractor(extractor_id)
        except ExtractorError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _extractor_response(copy.id)

    def _projects_using(extractor_id: str) -> list[str]:
        """Names of projects whose next run would use this extractor, saved or from their last run."""
        return [
            item.name for item in list_projects(root=projects_root)
            if (chosen := project_extractor(item.path).extractor) is not None and chosen.id == extractor_id
        ]

    @app.get(f"/{token}/api/extractors/{{extractor_id}}/projects")
    async def extractor_projects(extractor_id: str):
        """Projects that would be left without this extractor if it were deleted. Never opens the key vault."""
        if get_extractor(extractor_id) is None:
            raise HTTPException(status_code=404, detail="That extractor no longer exists.")
        return {"projects": _projects_using(extractor_id)}

    @app.post(f"/{token}/extractors/{{extractor_id}}/delete")
    async def remove_extractor(extractor_id: str):
        # Check first so a stale request returns 404 without scanning projects.
        if get_extractor(extractor_id) is None:
            raise HTTPException(status_code=404, detail="That extractor no longer exists.")
        affected = _projects_using(extractor_id)
        try:
            removed = delete_extractor(extractor_id)
        except ExtractorError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        warning = None
        if runtime.has_key(removed):
            try:
                await asyncio.to_thread(runtime.vault.delete, removed.id)
            except VaultUnavailable as exc:
                warning = f"The extractor was deleted, but its saved key could not be removed. {exc}"
        return {"deleted": True, "projects": affected, "warning": warning}

    @app.post(f"/{token}/extractors/{{extractor_id}}/key")
    async def replace_extractor_key(extractor_id: str, api_key: str = Form(...)):
        extractor = get_extractor(extractor_id)
        if extractor is None:
            raise HTTPException(status_code=404, detail="That extractor no longer exists.")
        if not api_key.strip():
            raise HTTPException(status_code=400, detail="Enter an API key.")
        warning = await asyncio.to_thread(_save_key, extractor, api_key)
        return {"saved": warning is None, "warning": warning}

    @app.post(f"/{token}/extractors/{{extractor_id}}/key/delete")
    async def remove_extractor_key(extractor_id: str):
        extractor = get_extractor(extractor_id)
        if extractor is None:
            raise HTTPException(status_code=404, detail="That extractor no longer exists.")
        try:
            await asyncio.to_thread(runtime.vault.delete, extractor.id)
        except VaultUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        set_key_saved(extractor.id, False)
        return {"deleted": True}

    @app.post(f"/{token}/projects")
    async def new_project(name: str = Form(...)):
        try:
            project = create_project(name, root=projects_root)
        except ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(f"/{token}/projects/{project.project_id}", status_code=303)

    @app.get(f"/{token}/api/projects")
    async def project_index():
        return [_project_index_snapshot(item) for item in list_projects(root=projects_root)]

    @app.get(f"/{token}/api/extractors/{{extractor_id}}/models")
    async def extractor_models(extractor_id: str):
        """Model suggestions for an extractor, using its key (may open the credential store once)."""
        extractor = get_extractor(extractor_id)
        if extractor is None:
            raise HTTPException(status_code=404, detail="That extractor no longer exists.")
        try:
            key = await asyncio.to_thread(runtime.key_for, extractor)
            if extractor.requires_key and not key:
                return []
            options = await asyncio.to_thread(list_live_models, key or "", extractor.provider)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return [{"id": option.name, "name": option.display_name} for option in options]

    @app.get(f"/{token}/api/models/openrouter/endpoints")
    async def openrouter_model_endpoints(model: str, extractor_id: str = ""):
        # The listing is public: send only a key already in memory, never read the store.
        key = runtime.vault.cached(extractor_id) if extractor_id else None
        try:
            normalized = normalize_model_name(model, "openrouter")
            return await asyncio.to_thread(list_model_endpoints, normalized, api_key=key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get(f"/{token}/projects/{{project_id}}", response_class=HTMLResponse)
    async def project_page(request: Request, project_id: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return TEMPLATES.TemplateResponse(
            request,
            "project.html",
            _project_context(request, runtime, project),
        )

    @app.post(f"/{token}/projects/{{project_id}}/reset")
    async def reset_project(project_id: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        job = runtime.jobs.get(project_id)
        if job and job.status in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="Cancel the active run before clearing outputs.")
        reset_project_analysis(project)
        with runtime._jobs_lock:
            runtime.jobs.pop(project_id, None)
        return {"reset": True}

    @app.post(f"/{token}/projects/{{project_id}}/delete")
    async def delete_project_route(project_id: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        job = runtime.jobs.get(project_id)
        if job and job.status in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="Cancel the active run before deleting this project.")
        delete_project(project)
        with runtime._jobs_lock:
            runtime.jobs.pop(project_id, None)
        return {"deleted": True}

    @app.post(f"/{token}/projects/{{project_id}}/rename")
    async def rename_project_route(project_id: str, name: str = Form(...)):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            renamed = rename_project(project, name)
        except ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"renamed": True, "name": renamed.name}

    @app.post(f"/{token}/projects/{{project_id}}/responses/delete")
    async def delete_response_route(project_id: str, source_name: str = Form(...)):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        job = runtime.jobs.get(project_id)
        if job and job.status in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="Cancel the active run before deleting a response.")
        source_name = source_name.strip()
        if not source_name or Path(source_name).name != source_name or not is_source_name(source_name):
            raise HTTPException(status_code=400, detail="Choose one source's response to delete.")
        if not delete_extraction(project.path, source_name):
            raise HTTPException(status_code=404, detail="That source does not have a saved extraction response.")
        return {"deleted": True, "source_name": source_name}

    @app.get(f"/{token}/api/projects/{{project_id}}")
    async def project_status(project_id: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        app_settings = load_app_settings()
        payload = project_snapshot(project)
        payload["pdf_parser"] = app_settings.pdf_parser
        payload["pdf_parser_label"] = PDF_PARSER_LABELS[app_settings.pdf_parser]
        job = runtime.jobs.get(project_id)
        payload["job"] = job.snapshot() if job else None
        return payload

    @app.post(f"/{token}/projects/{{project_id}}/uploads")
    async def upload_files(project_id: str, files: list[UploadFile] = File(...)):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if len(files) > 100:
            raise HTTPException(status_code=413, detail="Upload at most 100 files at a time.")
        saved = []
        try:
            for upload in files:
                saved.append(
                    save_upload(
                        project,
                        upload.filename or "",
                        upload.file,
                        max_bytes=load_app_settings().max_upload_size_mb * 1024 * 1024,
                    ).name
                )
        except UploadTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            for upload in files:
                await upload.close()
        return {"saved": saved}

    @app.post(f"/{token}/projects/{{project_id}}/instructions")
    async def save_instructions(
        project_id: str,
        instructions: str = Form(...),
        confirm_reset: Optional[str] = Form(None),
    ):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        job = runtime.jobs.get(project_id)
        if job and job.status in {"queued", "running"}:
            raise HTTPException(
                status_code=409,
                detail="Wait for the current run to finish before changing extraction instructions.",
            )

        try:
            result = update_project_instructions(
                project,
                instructions,
                confirm_reset=confirm_reset == "yes",
            )
        except AnalysisResetRequired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if result["reset"]:
            with runtime._jobs_lock:
                runtime.jobs.pop(project_id, None)
        return result

    @app.post(f"/{token}/projects/{{project_id}}/schema")
    async def save_schema(
        project_id: str,
        schema_text: str = Form(...),
        confirm_reset: Optional[str] = Form(None),
    ):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        job = runtime.jobs.get(project_id)
        if job and job.status in {"queued", "running"}:
            raise HTTPException(
                status_code=409,
                detail="Wait for the current run to finish before changing the extraction schema.",
            )

        try:
            result = update_project_schema(
                project,
                schema_text,
                confirm_reset=confirm_reset == "yes",
            )
        except AnalysisResetRequired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if result["reset"]:
            with runtime._jobs_lock:
                runtime.jobs.pop(project_id, None)
        return result

    @app.post(f"/{token}/projects/{{project_id}}/settings")
    async def save_project_settings_route(project_id: str, extractor_id: str = Form(...)):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        extractor = get_extractor(extractor_id)
        if extractor is None:
            raise HTTPException(status_code=400, detail="That extractor no longer exists. Choose another.")
        save_selected_extractor_id(project.path, extractor.id)
        return {"saved": True, "extractor": _extractor_view(runtime, extractor)}

    async def _prepare_run(project: ManagedProject, extractor_id: str | None) -> tuple[RunSettings, str | None]:
        """The settings and key for a run or retry with the chosen extractor."""
        if extractor_id:
            extractor = get_extractor(extractor_id)
            if extractor is None:
                raise ValueError("That extractor no longer exists. Choose another.")
            save_selected_extractor_id(project.path, extractor.id)
        else:
            chosen = project_extractor(project.path)
            if chosen.extractor is None:
                raise ValueError(
                    "The extractor used for this project was deleted. Choose another."
                    if chosen.deleted else "Choose an extractor for this project before running."
                )
            extractor = chosen.extractor
        settings = run_settings_for(extractor, load_app_settings())
        try:
            key = await asyncio.to_thread(runtime.key_for, extractor)
        except VaultUnavailable as exc:
            raise ValueError(str(exc)) from exc
        if extractor.requires_key and not key:
            raise ValueError(f"Add an API key for the “{extractor.name}” extractor in Settings before running.")
        return settings, key

    @app.post(f"/{token}/projects/{{project_id}}/run")
    async def start_run(project_id: str, extractor_id: Optional[str] = Form(None)):
        try:
            project = runtime.project(project_id)
            settings, key = await _prepare_run(project, extractor_id)
            job = runtime.start_job(project, "run", settings, key)
        except (ProjectError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"status": job.status}, status_code=202)

    @app.post(f"/{token}/projects/{{project_id}}/retry")
    async def start_retry(project_id: str, extractor_id: Optional[str] = Form(None)):
        try:
            project = runtime.project(project_id)
            settings, key = await _prepare_run(project, extractor_id)
            job = runtime.start_job(project, "retry", settings, key)
        except (ProjectError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"status": job.status}, status_code=202)

    @app.post(f"/{token}/projects/{{project_id}}/regenerate-report")
    async def start_report_regeneration(project_id: str):
        try:
            project = runtime.project(project_id)
            existing = load_run_settings(project.path) or RunSettings(primary_model="")
            job = runtime.start_job(project, "report", existing)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"status": job.status}, status_code=202)

    @app.post(f"/{token}/projects/{{project_id}}/cancel")
    async def cancel(project_id: str):
        job = runtime.jobs.get(project_id)
        if not job or job.status not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="This project is not running.")
        job.cancel_event.set()
        job.emit("status", status="cancelling", message="Cancelling run")
        return {"status": "cancelling"}

    @app.get(f"/{token}/projects/{{project_id}}/events")
    async def events(request: Request, project_id: str):
        try:
            runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        async def stream():
            cursor = 0
            while True:
                job = runtime.jobs.get(project_id)
                if job:
                    snapshot = job.snapshot()
                    new_events = snapshot["events"][cursor:]
                    for event in new_events:
                        cursor += 1
                        yield f"event: {event['event']}\ndata: {json.dumps(event)}\n\n"
                    if snapshot["status"] in {"complete", "failed", "cancelled"} and cursor >= len(snapshot["events"]):
                        break
                if await request.is_disconnected():
                    break
                await asyncio.sleep(0.2)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get(f"/{token}/projects/{{project_id}}/report")
    async def report(project_id: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = project.path / "output" / "report.html"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Report not found.")
        return FileResponse(path, media_type="text/html")

    @app.get(f"/{token}/projects/{{project_id}}/zcurve-plot")
    async def zcurve_plot(project_id: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = project.path / "output" / "zcurve_plot.png"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Z-curve plot not found.")
        return FileResponse(path, media_type="image/png")

    @app.get(f"/{token}/projects/{{project_id}}/sources/{{name}}")
    async def source_file(project_id: str, name: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        sources = project.path / "sources"
        fmt = source_format(name)
        if Path(name).name != name or fmt is None:
            raise HTTPException(status_code=404, detail="Source not found.")
        try:
            path = (sources / name).resolve(strict=True)
            real_sources = sources.resolve(strict=True)
        except (OSError, RuntimeError):
            raise HTTPException(status_code=404, detail="Source not found.") from None
        # Refuse symlinks that point outside the project's sources folder.
        if path.parent != real_sources or not path.is_file():
            raise HTTPException(status_code=404, detail="Source not found.")
        if fmt == "xml":
            # Shown as plain text: rendering XML on this origin could run markup it contains.
            return FileResponse(
                path,
                media_type="text/plain; charset=utf-8",
                content_disposition_type="inline",
                filename=name,
                headers={"X-Content-Type-Options": "nosniff"},
            )
        return FileResponse(path, media_type="application/pdf", content_disposition_type="inline", filename=name)

    @app.get(f"/{token}/projects/{{project_id}}/spreadsheet/{{name}}")
    async def download_spreadsheet_safe_csv(project_id: str, name: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        path = project.path / "output" / name
        if name not in SPREADSHEET_SAFE_FILES or not path.is_file():
            raise HTTPException(status_code=404, detail="Spreadsheet download not found.")
        payload = await asyncio.to_thread(_spreadsheet_safe_download, path)
        return Response(
            payload,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{spreadsheet_safe_name(name)}"'},
        )

    @app.get(f"/{token}/projects/{{project_id}}/results.zip")
    async def download_results(project_id: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        archive = await asyncio.to_thread(_results_zip, project)
        filename = "".join(char if char.isalnum() or char in "-_" else "-" for char in project.name).strip("-")
        return Response(
            archive,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename or "auto-zcurve"}-results.zip"'},
        )

    @app.post(f"/{token}/projects/{{project_id}}/open-folder")
    async def open_folder(project_id: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            if platform.system() == "Windows":
                os.startfile(str(project.path))  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(
                    ["open", str(project.path)],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    ["xdg-open", str(project.path)],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except (AttributeError, OSError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Could not open the project folder on this computer: {exc}",
            ) from exc
        return {"opened": True}

    return app


def launch_web(*, open_browser: bool = True, port: int | None = None) -> int:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("The web interface dependencies are not installed.") from exc

    token = secrets.token_urlsafe(32)
    if port is None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
    app = create_app(token=token)
    threading.Thread(target=app.state.runtime.check_for_update, daemon=True).start()
    url = f"http://127.0.0.1:{port}/{token}/"
    print(f"auto-zcurve is running locally at {url}")
    print("Press Ctrl+C to stop it.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    return 0
