from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from .artifacts import (
    latest_by_source,
    load_extractions,
    parse_zcurve_summary,
    read_disclosure_summary,
    read_zcurve_summary,
)
from .config import DEFAULTS, load_run_settings
from .models import DEFAULT_MODEL
from .paths import DEFAULT_INSTRUCTIONS, DEFAULT_SCHEMA
from .schema import build_response_schema, parse_extraction_schema


PROJECT_ID_RE = re.compile(r"^[a-f0-9]{16}$")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
PROJECT_INSTRUCTIONS_NAME = "extraction_instructions.md"
PROJECT_SCHEMA_NAME = "extraction_schema.yml"
MAX_INSTRUCTIONS_CHARS = 100_000
MAX_SCHEMA_CHARS = 200_000


class ProjectError(ValueError):
    pass


class UploadTooLarge(ProjectError):
    pass


class AnalysisResetRequired(ProjectError):
    pass


@dataclass(frozen=True)
class ManagedProject:
    project_id: str
    name: str
    path: Path
    created_at: str


def app_data_dir() -> Path:
    override = os.environ.get("AUTO_ZCURVE_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "Auto Z-Curve"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Auto Z-Curve"
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "auto-zcurve"


def projects_dir() -> Path:
    return app_data_dir() / "projects"


def _metadata_path(path: Path) -> Path:
    return path / ".auto_zcurve" / "project.json"


def _write_metadata(project: ManagedProject) -> None:
    path = _metadata_path(project.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": project.project_id,
                "name": project.name,
                "created_at": project.created_at,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def create_project(name: str, *, root: Path | None = None) -> ManagedProject:
    clean_name = " ".join(name.split()).strip()
    if not clean_name:
        raise ProjectError("Enter a project name.")
    if len(clean_name) > 100:
        raise ProjectError("Project names must be 100 characters or fewer.")

    base = (root or projects_dir()).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    project_id = secrets.token_hex(8)
    path = base / project_id
    (path / "sources").mkdir(parents=True)
    (path / "output" / "raw").mkdir(parents=True)
    shutil.copyfile(DEFAULT_SCHEMA, path / PROJECT_SCHEMA_NAME)
    shutil.copyfile(DEFAULT_INSTRUCTIONS, path / PROJECT_INSTRUCTIONS_NAME)
    project = ManagedProject(
        project_id=project_id,
        name=clean_name,
        path=path,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    _write_metadata(project)
    return project


def get_project(project_id: str, *, root: Path | None = None) -> ManagedProject:
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise ProjectError("Invalid project identifier.")
    base = (root or projects_dir()).expanduser().resolve()
    path = (base / project_id).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ProjectError("Invalid project path.") from exc
    metadata_path = _metadata_path(path)
    if not metadata_path.is_file():
        raise ProjectError("Project not found.")
    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError("Project metadata could not be read.") from exc
    return ManagedProject(
        project_id=project_id,
        name=str(raw.get("name") or "Untitled project"),
        path=path,
        created_at=str(raw.get("created_at") or ""),
    )


def list_projects(*, root: Path | None = None) -> list[ManagedProject]:
    base = (root or projects_dir()).expanduser().resolve()
    if not base.exists():
        return []
    found: list[ManagedProject] = []
    for path in base.iterdir():
        if not path.is_dir() or not PROJECT_ID_RE.fullmatch(path.name):
            continue
        try:
            found.append(get_project(path.name, root=base))
        except ProjectError:
            continue
    return sorted(found, key=lambda item: item.created_at, reverse=True)


def safe_upload_name(filename: str) -> str:
    basename = Path(filename.replace("\\", "/")).name
    cleaned = SAFE_NAME_RE.sub("_", basename).strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        raise ProjectError("A PDF filename is required.")
    if len(cleaned) > 180:
        stem = Path(cleaned).stem[:160]
        cleaned = stem + Path(cleaned).suffix
    if Path(cleaned).suffix.lower() != ".pdf":
        raise ProjectError(f"{basename or 'File'} is not a PDF.")
    return cleaned


def unique_destination(sources_dir: Path, filename: str) -> Path:
    candidate = sources_dir / filename
    if not candidate.exists():
        return candidate
    stem, suffix = Path(filename).stem, Path(filename).suffix
    counter = 2
    while True:
        candidate = sources_dir / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def save_upload(
    project: ManagedProject,
    filename: str,
    stream: BinaryIO,
    *,
    max_bytes: int = DEFAULTS["max_upload_size_mb"] * 1024 * 1024,
) -> Path:
    safe_name = safe_upload_name(filename)
    destination = unique_destination(project.path / "sources", safe_name)
    partial = destination.with_name(destination.name + ".uploading")
    written = 0
    try:
        with partial.open("xb") as handle:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise UploadTooLarge(
                        f"{safe_name} exceeds the {max_bytes // (1024 * 1024)} MB upload limit."
                    )
                handle.write(chunk)
        if written < 4:
            raise ProjectError(f"{safe_name} is empty or not a valid PDF.")
        with partial.open("rb") as handle:
            if handle.read(4) != b"%PDF":
                raise ProjectError(f"{safe_name} is not a valid PDF.")
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return destination


def project_instruction_path(project_dir: Path) -> Path:
    project_path = project_dir / PROJECT_INSTRUCTIONS_NAME
    return project_path if project_path.is_file() else DEFAULT_INSTRUCTIONS


def read_project_instructions(project: ManagedProject) -> str:
    return project_instruction_path(project.path).read_text(encoding="utf-8")


def read_project_schema(project: ManagedProject) -> str:
    return (project.path / PROJECT_SCHEMA_NAME).read_text(encoding="utf-8")


def project_has_analysis_results(project: ManagedProject) -> bool:
    output = project.path / "output"
    return output.is_dir() and any(path.is_file() for path in output.rglob("*"))


def _reset_project_analysis(project: ManagedProject) -> None:
    output = project.path / "output"
    shutil.rmtree(output)
    (output / "raw").mkdir(parents=True)


def update_project_instructions(
    project: ManagedProject,
    instructions: str,
    *,
    confirm_reset: bool = False,
) -> dict[str, bool]:
    normalized = instructions.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    if not normalized:
        raise ProjectError("Extraction instructions cannot be empty.")
    if len(normalized) > MAX_INSTRUCTIONS_CHARS:
        raise ProjectError(
            f"Extraction instructions must be {MAX_INSTRUCTIONS_CHARS:,} characters or fewer."
        )
    normalized += "\n"
    current = read_project_instructions(project).replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"
    if normalized == current:
        return {"changed": False, "reset": False}

    has_results = project_has_analysis_results(project)
    if has_results and not confirm_reset:
        raise AnalysisResetRequired(
            "Changing extraction instructions will delete the existing report, extraction results, "
            "and processing logs. Every PDF will need to be processed again."
        )

    if has_results:
        _reset_project_analysis(project)

    (project.path / PROJECT_INSTRUCTIONS_NAME).write_text(normalized, encoding="utf-8")
    return {"changed": True, "reset": has_results}


def update_project_schema(
    project: ManagedProject,
    schema_text: str,
    *,
    confirm_reset: bool = False,
) -> dict[str, bool]:
    normalized = schema_text.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    if not normalized:
        raise ProjectError("The extraction schema cannot be empty.")
    if len(normalized) > MAX_SCHEMA_CHARS:
        raise ProjectError(
            f"The extraction schema must be {MAX_SCHEMA_CHARS:,} characters or fewer."
        )
    normalized += "\n"

    schema_path = project.path / PROJECT_SCHEMA_NAME
    try:
        parsed = parse_extraction_schema(normalized, path=schema_path)
        build_response_schema(parsed)
    except (RuntimeError, ValueError) as exc:
        raise ProjectError(str(exc)) from exc

    current = read_project_schema(project).replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"
    if normalized == current:
        return {"changed": False, "reset": False}

    has_results = project_has_analysis_results(project)
    if has_results and not confirm_reset:
        raise AnalysisResetRequired(
            "Changing the extraction schema will delete the existing report, extraction results, "
            "and processing logs. Every PDF will need to be processed again."
        )

    if has_results:
        _reset_project_analysis(project)

    schema_path.write_text(normalized, encoding="utf-8")
    return {"changed": True, "reset": has_results}


def project_snapshot(project: ManagedProject) -> dict:
    sources = sorted(project.path.joinpath("sources").glob("*.pdf"))
    records = latest_by_source(load_extractions(project.path))
    articles = []
    for source in sources:
        record = records.get(source.name, {})
        status = str(record.get("status") or "ready")
        articles.append(
            {
                "name": source.name,
                "size": source.stat().st_size,
                "status": status,
                "error": str(record.get("error") or ""),
                "effects": int(record.get("effects") or 0),
                "input_tokens": int(record.get("input_tokens") or 0),
                "output_tokens": int(record.get("output_tokens") or 0),
                "total_tokens": int(record.get("total_tokens") or 0),
            }
        )
    settings = load_run_settings(project.path)
    report = project.path / "output" / "report.html"
    zcurve_text = read_zcurve_summary(project.path) if report.is_file() else None
    disclosure_rows, usable_inputs = read_disclosure_summary(project.path) if report.is_file() else (0, 0)
    parsed_summary = parse_zcurve_summary(zcurve_text)
    report_summary = None
    if report.is_file():
        report_summary = {
            "extracted_effects": disclosure_rows,
            "usable_zcurve_inputs": usable_inputs,
            "execution": parsed_summary["execution"],
            "metrics": parsed_summary["metrics"],
            "text": zcurve_text,
        }
    return {
        "id": project.project_id,
        "name": project.name,
        "created_at": project.created_at,
        "articles": articles,
        "pdf_count": len(articles),
        "failed_count": sum(item["status"] == "error" for item in articles),
        "successful_count": sum(item["status"] == "ok" for item in articles),
        "has_extractions": bool(records),
        "has_analysis_results": project_has_analysis_results(project),
        "total_tokens": sum(item["total_tokens"] for item in articles),
        "has_report": report.is_file(),
        "report_summary": report_summary,
        "instructions": read_project_instructions(project),
        "model": settings.primary_model if settings else DEFAULT_MODEL,
        "parallel_requests": settings.parallel_requests if settings else DEFAULTS["parallel_requests"],
    }
