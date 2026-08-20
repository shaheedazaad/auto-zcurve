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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
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
    normalize_reasoning_effort,
    normalize_service_tier,
    save_app_settings,
    save_run_settings,
)
from .credentials import (
    CredentialStoreUnavailable,
    credential_store_available,
    delete_saved_api_key,
    load_saved_api_key,
    saved_api_key_configured,
    save_api_key,
)
from .models import (
    DEFAULT_MODEL,
    fallback_models,
    list_live_models,
    model_request_defaults,
    normalize_model_name,
    resolve_input_mode,
    validate_model_option,
)
from .preflight import run_preflight
from .projects import (
    AnalysisResetRequired,
    ManagedProject,
    ProjectError,
    UploadTooLarge,
    create_project,
    delete_project,
    get_project,
    list_projects,
    project_snapshot,
    read_project_schema,
    rename_project,
    reset_project_analysis,
    save_upload,
    update_project_instructions,
    update_project_schema,
)
from .runner import RunCancelled, regenerate_report, retry_project, run_project
from .security import redact_secrets
from .user_facing import classify_error
from .providers import PROVIDERS, normalize_provider, provider_label


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
        host = request.headers.get("host", "").split(":", 1)[0].lower()
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
    def __init__(self, job: Job, secret: str | None = None) -> None:
        self.job = job
        self.secret = secret

    def print(self, *parts: object) -> None:
        self.job.emit("log", message=redact_secrets(" ".join(str(part) for part in parts), [self.secret]))

    def title(self, text: str, subtitle: str | None = None) -> None:
        self.job.emit(
            "log",
            message=redact_secrets(text if subtitle is None else f"{text} — {subtitle}", [self.secret]),
        )

    def info(self, text: str) -> None:
        self.job.emit("log", level="info", message=redact_secrets(text, [self.secret]))

    def warn(self, text: str) -> None:
        self.job.emit("log", level="warning", message=redact_secrets(text, [self.secret]))

    def error(self, text: str) -> None:
        self.job.emit("log", level="error", message=redact_secrets(text, [self.secret]))

    def success(self, text: str) -> None:
        self.job.emit("log", level="success", message=redact_secrets(text, [self.secret]))

    def table(self, title: str, columns, rows) -> None:
        self.job.emit(
            "table",
            title=title,
            columns=list(columns),
            rows=[
                [redact_secrets(value, [self.secret]) for value in row]
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
        self.session_api_keys: dict[str, str] = {}
        self.key_warnings: dict[str, str] = {}
        self.jobs: dict[str, Job] = {}
        self.update_version: str | None = None
        self._jobs_lock = threading.Lock()

    def project(self, project_id: str) -> ManagedProject:
        return get_project(project_id, root=self.projects_root)

    @property
    def session_api_key(self) -> str | None:
        """Compatibility alias for the legacy Gemini-only runtime field."""

        return self.session_api_keys.get("gemini")

    @session_api_key.setter
    def session_api_key(self, value: str | None) -> None:
        if value:
            self.session_api_keys["gemini"] = value
        else:
            self.session_api_keys.pop("gemini", None)

    @property
    def key_warning(self) -> str | None:
        return self.key_warnings.get("gemini")

    @key_warning.setter
    def key_warning(self, value: str | None) -> None:
        if value:
            self.key_warnings["gemini"] = value
        else:
            self.key_warnings.pop("gemini", None)

    def api_key(self, provider: str = "gemini") -> str | None:
        return self.session_api_keys.get(normalize_provider(provider))

    def secrets(self) -> list[str]:
        return list(self.session_api_keys.values())

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

    def start_job(self, project: ManagedProject, action: str, settings: RunSettings) -> Job:
        with self._jobs_lock:
            existing = self.jobs.get(project.project_id)
            if existing and existing.status in {"queued", "running"}:
                raise ProjectError("This project is already running.")
            job = Job(project.project_id, action)
            self.jobs[project.project_id] = job
        thread = threading.Thread(
            target=self._run_job,
            args=(job, project, action, settings),
            daemon=True,
            name=f"auto-zcurve-{project.project_id}",
        )
        thread.start()
        return job

    def _run_job(self, job: Job, project: ManagedProject, action: str, settings: RunSettings) -> None:
        job.status = "running"
        label = "Report regeneration" if action == "report" else action.title()
        job.emit("status", status="running", message=f"{label} started")
        try:
            api_key = self.api_key(settings.provider)
            if action != "report" and not api_key:
                raise RuntimeError(f"A {provider_label(settings.provider)} API key is required.")
            console = WebConsole(job, api_key)
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
                    api_key=api_key,
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
                    api_key=api_key,
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


def _project_context(request: Request, runtime: WebRuntime, project: ManagedProject) -> dict:
    snapshot = project_snapshot(project)
    app_settings = load_app_settings()
    snapshot["pdf_parser"] = app_settings.pdf_parser
    snapshot["pdf_parser_label"] = PDF_PARSER_LABELS[app_settings.pdf_parser]
    snapshot["max_upload_size_mb"] = app_settings.max_upload_size_mb
    selected_provider = normalize_provider(snapshot.get("provider"))
    job = runtime.jobs.get(project.project_id)
    return {
        "request": request,
        "token": runtime.token,
        "version": __version__,
        "project": snapshot,
        "job": job.snapshot() if job else None,
        "schema_text": read_project_schema(project),
        "models": fallback_models(selected_provider),
        "providers": [(name, provider_label(name)) for name in PROVIDERS],
        "provider_key_status": {name: runtime.api_key(name) is not None for name in PROVIDERS},
        "provider_saved_key_status": {
            name: saved_api_key_configured(name) for name in PROVIDERS
        },
        "provider_label": provider_label(selected_provider),
        "has_api_key": runtime.api_key(selected_provider) is not None,
        "has_saved_api_key": saved_api_key_configured(selected_provider),
        "keyring_available": credential_store_available(),
        "is_macos": platform.system() == "Darwin",
        "key_warning": runtime.key_warnings.get(selected_provider),
        "update_version": runtime.update_version,
    }


def _credential_context(runtime: WebRuntime) -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "label": provider_label(name),
            "has_api_key": runtime.api_key(name) is not None,
            "has_saved_api_key": saved_api_key_configured(name),
            "key_warning": runtime.key_warnings.get(name),
        }
        for name in PROVIDERS
    ]


def _project_index_snapshot(project: ManagedProject) -> dict:
    sources = sorted(project.path.joinpath("sources").glob("*.pdf"))
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


def create_app(*, token: str | None = None, projects_root: Path | None = None) -> FastAPI:
    token = token or secrets.token_urlsafe(32)
    runtime = WebRuntime(token, projects_root=projects_root)
    app = FastAPI(title="Auto Z-Curve", docs_url=None, redoc_url=None, openapi_url=None)
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
                "credential_providers": _credential_context(runtime),
                "keyring_available": credential_store_available(),
                "is_macos": platform.system() == "Darwin",
                "app_settings": load_app_settings(),
                "pdf_parser_labels": PDF_PARSER_LABELS,
                "reasoning_effort_labels": REASONING_EFFORT_LABELS,
                "service_tier_labels": SERVICE_TIER_LABELS,
                "update_version": runtime.update_version,
            },
        )

    @app.post(f"/{token}/settings")
    async def update_settings(
        pdf_parser: str = Form(DEFAULTS["pdf_parser"]),
        parallel_requests: int = Form(DEFAULTS["parallel_requests"]),
        request_timeout_sec: int = Form(DEFAULTS["request_timeout_sec"]),
        max_upload_size_mb: int = Form(DEFAULTS["max_upload_size_mb"]),
        request_delay_sec: int = Form(DEFAULTS["request_delay_sec"]),
        reasoning_effort: str = Form(DEFAULTS["reasoning_effort"]),
        service_tier: str = Form(DEFAULTS["service_tier"]),
    ):
        try:
            settings = AppSettings(
                pdf_parser=normalize_pdf_parser(pdf_parser),
                parallel_requests=max(1, min(int(parallel_requests), 32)),
                request_delay_sec=max(0, min(int(request_delay_sec), 3600)),
                request_timeout_sec=max(30, min(int(request_timeout_sec), 3600)),
                max_upload_size_mb=max(1, min(int(max_upload_size_mb), 512)),
                reasoning_effort=normalize_reasoning_effort(reasoning_effort),
                service_tier=normalize_service_tier(service_tier),
            )
            save_app_settings(settings)
        except (OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "saved": True,
            "pdf_parser": settings.pdf_parser,
            "pdf_parser_label": PDF_PARSER_LABELS[settings.pdf_parser],
            "reasoning_effort": settings.reasoning_effort,
            "reasoning_effort_label": REASONING_EFFORT_LABELS[settings.reasoning_effort],
            "service_tier": settings.service_tier,
            "service_tier_label": SERVICE_TIER_LABELS[settings.service_tier],
            "request_delay_sec": settings.request_delay_sec,
        }

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

    @app.get(f"/{token}/api/models/{{provider}}")
    async def model_catalog(provider: str):
        try:
            selected = normalize_provider(provider)
            key = runtime.api_key(selected) or ""
            if selected == "gemini" and not key:
                options = fallback_models(selected)
            else:
                options = list_live_models(key, selected)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        parser = load_app_settings().pdf_parser
        catalog = []
        for option in options:
            try:
                input_mode = resolve_input_mode(selected, option, parser)
            except ValueError:
                continue
            catalog.append(
                {
                    "id": option.name,
                    "name": option.display_name,
                    "description": option.description,
                    "provider": option.provider,
                    "input_mode": input_mode,
                    "context_length": option.context_length,
                    "parallel_requests": option.parallel_requests,
                    "request_delay_sec": option.request_delay_sec,
                }
            )
        return catalog

    @app.post(f"/{token}/api/models/openrouter/validate")
    async def validate_openrouter_model(model: str = Form(...)):
        key = runtime.api_key("openrouter")
        if not key:
            raise HTTPException(status_code=400, detail="An OpenRouter API key is required to validate a model.")
        try:
            option = validate_model_option("openrouter", model, key)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"valid": True, "id": option.name, "parallel_requests": option.parallel_requests,
                "request_delay_sec": option.request_delay_sec}

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
        if not source_name or Path(source_name).name != source_name or not source_name.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Choose one PDF response to delete.")
        if not delete_extraction(project.path, source_name):
            raise HTTPException(status_code=404, detail="That PDF does not have a saved extraction response.")
        return {"deleted": True, "source_name": source_name}

    @app.get(f"/{token}/api/projects/{{project_id}}")
    async def project_status(project_id: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = project_snapshot(project)
        app_settings = load_app_settings()
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
            raise HTTPException(status_code=413, detail="Upload at most 100 PDFs at a time.")
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

    @app.post(f"/{token}/credentials")
    async def credentials(
        api_key: str = Form(...),
        provider: str = Form("gemini"),
        remember: Optional[str] = Form(None),
    ):
        try:
            selected = normalize_provider(provider)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        key = api_key.strip()
        if not key:
            raise HTTPException(status_code=400, detail=f"Enter a {provider_label(selected)} API key.")
        runtime.session_api_keys[selected] = key
        runtime.key_warnings.pop(selected, None)
        if remember:
            try:
                save_api_key(key) if selected == "gemini" else save_api_key(key, selected)
            except CredentialStoreUnavailable as exc:
                runtime.key_warnings[selected] = str(exc)
        warning = runtime.key_warnings.get(selected)
        return {
            "provider": selected,
            "saved": bool(remember and warning is None),
            "session_only": not remember or warning is not None,
            "warning": warning,
        }

    @app.post(f"/{token}/credentials/load")
    async def load_credentials(provider: str = Form("gemini")):
        try:
            selected = normalize_provider(provider)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        key = load_saved_api_key() if selected == "gemini" else load_saved_api_key(selected)
        if not key:
            raise HTTPException(
                status_code=404,
                detail=f"No saved {provider_label(selected)} API key was found in the credential store.",
            )
        runtime.session_api_keys[selected] = key
        runtime.key_warnings.pop(selected, None)
        return {"loaded": True, "provider": selected}

    @app.post(f"/{token}/credentials/delete")
    async def delete_credentials(provider: str = Form("gemini")):
        try:
            selected = normalize_provider(provider)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        runtime.session_api_keys.pop(selected, None)
        runtime.key_warnings.pop(selected, None)
        deleted = delete_saved_api_key(selected)
        return {"deleted": deleted, "provider": selected}

    @app.post(f"/{token}/projects/{{project_id}}/settings")
    async def save_project_settings_route(
        project_id: str,
        provider: str = Form("gemini"),
        model: str = Form(DEFAULT_MODEL),
        parallel_requests: Optional[int] = Form(None),
        request_delay_sec: Optional[int] = Form(None),
    ):
        try:
            project = runtime.project(project_id)
            existing = load_run_settings(project.path)
            selected = normalize_provider(provider)
            normalized_model = normalize_model_name(model, selected)
            if selected == "openrouter":
                key = runtime.api_key(selected)
                if key is None:
                    raise ValueError("An OpenRouter API key is required to validate a model.")
                validate_model_option(selected, normalized_model, key)
            current = existing or RunSettings(primary_model=normalized_model, provider=selected)
            default_parallel, default_delay = model_request_defaults(normalized_model, selected)
            settings = RunSettings(
                primary_model=normalized_model,
                provider=selected,
                request_timeout_sec=current.request_timeout_sec,
                parallel_requests=max(1, min(int(parallel_requests if parallel_requests is not None else default_parallel), 32)),
                request_delay_sec=max(0, min(int(request_delay_sec if request_delay_sec is not None else default_delay), 3600)),
                max_upload_size_mb=current.max_upload_size_mb,
                effect_definition=current.effect_definition,
                pdf_parser=current.pdf_parser,
                reasoning_effort=current.reasoning_effort,
                service_tier=current.service_tier,
            )
            save_run_settings(project.path, settings)
        except (OSError, ProjectError, RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"saved": True, "parallel_requests": settings.parallel_requests, "request_delay_sec": settings.request_delay_sec}

    @app.post(f"/{token}/projects/{{project_id}}/run")
    async def start_run(
        project_id: str,
        provider: str = Form("gemini"),
        model: str = Form(DEFAULT_MODEL),
        parallel_requests: int | None = Form(None),
        request_delay_sec: int | None = Form(None),
    ):
        try:
            project = runtime.project(project_id)
            existing = load_run_settings(project.path)
            app_defaults = load_app_settings()
            selected = normalize_provider(provider)
            if runtime.api_key(selected) is None:
                raise ValueError(f"A {provider_label(selected)} API key is required.")
            if selected == "openrouter":
                validate_model_option(selected, model, runtime.api_key(selected) or "")
            default_parallel, default_delay = model_request_defaults(model, selected)
            use_saved_values = existing is not None and existing.provider == selected
            settings = RunSettings(
                primary_model=normalize_model_name(model, selected),
                provider=selected,
                request_timeout_sec=existing.request_timeout_sec if existing else app_defaults.request_timeout_sec,
                parallel_requests=max(1, min(int(parallel_requests if parallel_requests is not None else (existing.parallel_requests if use_saved_values else default_parallel)), 32)),
                request_delay_sec=max(0, min(int(request_delay_sec if request_delay_sec is not None else (existing.request_delay_sec if use_saved_values else default_delay)), 3600)),
                max_upload_size_mb=existing.max_upload_size_mb if existing else app_defaults.max_upload_size_mb,
                effect_definition=existing.effect_definition if existing else None,
                pdf_parser=app_defaults.pdf_parser,
                reasoning_effort=app_defaults.reasoning_effort,
                service_tier=app_defaults.service_tier,
            )
            job = runtime.start_job(project, "run", settings)
        except (ProjectError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"status": job.status}, status_code=202)

    @app.post(f"/{token}/projects/{{project_id}}/retry")
    async def start_retry(
        project_id: str,
        provider: Optional[str] = Form(None),
        model: Optional[str] = Form(None),
        parallel_requests: Optional[int] = Form(None),
        request_delay_sec: Optional[int] = Form(None),
    ):
        try:
            project = runtime.project(project_id)
            existing = load_run_settings(project.path)
            app_defaults = load_app_settings()
            if existing is None:
                existing = RunSettings(
                    primary_model=DEFAULT_MODEL,
                    request_timeout_sec=app_defaults.request_timeout_sec,
                    parallel_requests=app_defaults.parallel_requests,
                    max_upload_size_mb=app_defaults.max_upload_size_mb,
                    pdf_parser=app_defaults.pdf_parser,
                    reasoning_effort=app_defaults.reasoning_effort,
                    service_tier=app_defaults.service_tier,
                    request_delay_sec=app_defaults.request_delay_sec,
                )
            selected = normalize_provider(provider or existing.provider)
            selected_model = model or (
                existing.primary_model if existing.provider == selected else ""
            )
            if runtime.api_key(selected) is None:
                raise ValueError(f"A {provider_label(selected)} API key is required.")
            if selected == "openrouter":
                validate_model_option(selected, selected_model, runtime.api_key(selected) or "")
            default_parallel, default_delay = model_request_defaults(selected_model, selected)
            use_saved_values = existing.provider == selected
            settings = RunSettings(
                primary_model=normalize_model_name(selected_model, selected),
                provider=selected,
                request_timeout_sec=existing.request_timeout_sec,
                parallel_requests=max(1, min(int(parallel_requests if parallel_requests is not None else (existing.parallel_requests if use_saved_values else default_parallel)), 32)),
                request_delay_sec=max(0, min(int(request_delay_sec if request_delay_sec is not None else (existing.request_delay_sec if use_saved_values else default_delay)), 3600)),
                max_upload_size_mb=existing.max_upload_size_mb,
                effect_definition=existing.effect_definition,
                pdf_parser=app_defaults.pdf_parser,
                reasoning_effort=app_defaults.reasoning_effort,
                service_tier=app_defaults.service_tier,
            )
            job = runtime.start_job(project, "retry", settings)
        except (ProjectError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"status": job.status}, status_code=202)

    @app.post(f"/{token}/projects/{{project_id}}/regenerate-report")
    async def start_report_regeneration(project_id: str):
        try:
            project = runtime.project(project_id)
            existing = load_run_settings(project.path)
            if existing is None:
                existing = RunSettings(primary_model=DEFAULT_MODEL)
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

    @app.get(f"/{token}/projects/{{project_id}}/results.zip")
    async def download_results(project_id: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
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
        buffer.seek(0)
        filename = "".join(char if char.isalnum() or char in "-_" else "-" for char in project.name).strip("-")
        return StreamingResponse(
            buffer,
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
    print(f"Auto Z-Curve is running locally at {url}")
    print("Press Ctrl+C to stop it.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    return 0
