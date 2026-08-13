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
from .artifacts import latest_by_source, load_extractions
from .config import DEFAULTS, RunSettings, load_run_settings
from .credentials import (
    CredentialStoreUnavailable,
    credential_store_available,
    load_saved_api_key,
    saved_api_key_configured,
    save_api_key,
)
from .models import DEFAULT_MODEL, fallback_models, normalize_model_name
from .preflight import run_preflight
from .projects import (
    AnalysisResetRequired,
    ManagedProject,
    ProjectError,
    UploadTooLarge,
    create_project,
    get_project,
    list_projects,
    project_snapshot,
    read_project_schema,
    save_upload,
    update_project_instructions,
    update_project_schema,
)
from .runner import RunCancelled, regenerate_report, retry_project, run_project
from .security import redact_secrets
from .user_facing import classify_error


PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
STATIC_DIR = PACKAGE_DIR / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MAX_UPLOAD_REQUEST_BYTES = 512 * 1024 * 1024


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
        self.session_api_key: str | None = None
        self.key_warning: str | None = None
        self.jobs: dict[str, Job] = {}
        self.update_version: str | None = None
        self._jobs_lock = threading.Lock()

    def project(self, project_id: str) -> ManagedProject:
        return get_project(project_id, root=self.projects_root)

    def api_key(self) -> str | None:
        return self.session_api_key

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
            api_key = self.api_key()
            if action != "report" and not api_key:
                raise RuntimeError("A Gemini API key is required.")
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
                key: redact_secrets(value, [self.api_key()])
                for key, value in asdict(friendly).items()
                if value is not None
            }
            job.status = "failed"
            job.emit(
                "status",
                status="failed",
                message=redact_secrets(friendly.compact(), [self.api_key()]),
            )


def _project_context(request: Request, runtime: WebRuntime, project: ManagedProject) -> dict:
    snapshot = project_snapshot(project)
    job = runtime.jobs.get(project.project_id)
    return {
        "request": request,
        "token": runtime.token,
        "version": __version__,
        "project": snapshot,
        "job": job.snapshot() if job else None,
        "models": fallback_models(),
        "has_api_key": runtime.api_key() is not None,
        "has_saved_api_key": saved_api_key_configured(),
        "keyring_available": credential_store_available(),
        "is_macos": platform.system() == "Darwin",
        "key_warning": runtime.key_warning,
        "update_version": runtime.update_version,
    }


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
        if filename not in {"app.css", "app.js"}:
            raise HTTPException(status_code=404)
        return FileResponse(STATIC_DIR / filename)

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
                "has_api_key": runtime.session_api_key is not None,
                "has_saved_api_key": saved_api_key_configured(),
                "keyring_available": credential_store_available(),
                "is_macos": platform.system() == "Darwin",
                "key_warning": runtime.key_warning,
                "update_version": runtime.update_version,
            },
        )

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

    @app.get(f"/{token}/api/projects/{{project_id}}")
    async def project_status(project_id: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = project_snapshot(project)
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
                        max_bytes=DEFAULTS["max_upload_size_mb"] * 1024 * 1024,
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

    @app.get(f"/{token}/projects/{{project_id}}/schema", response_class=HTMLResponse)
    async def schema_page(request: Request, project_id: str):
        try:
            project = runtime.project(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        job = runtime.jobs.get(project_id)
        return TEMPLATES.TemplateResponse(
            request,
            "schema.html",
            {
                "token": token,
                "version": __version__,
                "project": project_snapshot(project),
                "schema_text": read_project_schema(project),
                "job_running": bool(job and job.status in {"queued", "running"}),
                "update_version": runtime.update_version,
            },
        )

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
    async def credentials(api_key: str = Form(...), remember: Optional[str] = Form(None)):
        key = api_key.strip()
        if not key:
            raise HTTPException(status_code=400, detail="Enter a Gemini API key.")
        runtime.session_api_key = key
        runtime.key_warning = None
        if remember:
            try:
                save_api_key(key)
            except CredentialStoreUnavailable as exc:
                runtime.key_warning = str(exc)
        return {
            "saved": bool(remember and runtime.key_warning is None),
            "session_only": not remember or runtime.key_warning is not None,
            "warning": runtime.key_warning,
        }

    @app.post(f"/{token}/credentials/load")
    async def load_credentials():
        key = load_saved_api_key()
        if not key:
            raise HTTPException(status_code=404, detail="No saved API key was found in the credential store.")
        runtime.session_api_key = key
        runtime.key_warning = None
        return {"loaded": True}

    @app.post(f"/{token}/projects/{{project_id}}/run")
    async def start_run(
        project_id: str,
        model: str = Form(DEFAULT_MODEL),
        parallel_requests: int = Form(DEFAULTS["parallel_requests"]),
    ):
        try:
            project = runtime.project(project_id)
            existing = load_run_settings(project.path)
            settings = RunSettings(
                primary_model=normalize_model_name(model),
                request_timeout_sec=existing.request_timeout_sec if existing else DEFAULTS["request_timeout_sec"],
                parallel_requests=max(1, min(int(parallel_requests), 32)),
                max_upload_size_mb=existing.max_upload_size_mb if existing else DEFAULTS["max_upload_size_mb"],
                effect_definition=existing.effect_definition if existing else None,
            )
            job = runtime.start_job(project, "run", settings)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"status": job.status}, status_code=202)

    @app.post(f"/{token}/projects/{{project_id}}/retry")
    async def start_retry(project_id: str):
        try:
            project = runtime.project(project_id)
            existing = load_run_settings(project.path)
            if existing is None:
                existing = RunSettings(primary_model=DEFAULT_MODEL)
            job = runtime.start_job(project, "retry", existing)
        except ProjectError as exc:
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
        job.emit("status", status="cancelling", message="Stopping after the current request")
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
        }
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if output.exists():
                for path in sorted(output.rglob("*")):
                    relative = path.relative_to(output)
                    is_raw_artifact = (
                        len(relative.parts) == 2
                        and relative.parts[0] == "raw"
                        and path.suffix == ".json"
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
        if platform.system() == "Windows":
            os.startfile(str(project.path))  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(project.path)])
        else:
            subprocess.Popen(["xdg-open", str(project.path)])
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
