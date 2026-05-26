from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .artifacts import load_extractions, read_zcurve_summary
from .credentials import load_saved_api_key
from .env import load_dotenv
from .paths import DEFAULT_SCHEMA
from .preflight import check_r_packages, check_system_tools, missing_python_dependencies
from .runner import RunSummary


DependencyCheck = Callable[[], tuple[list[str], list[str], list[str]]]


@dataclass(frozen=True)
class UserFacingError:
    title: str
    explanation: str
    next_action: str
    technical_detail: str | None = None

    def compact(self) -> str:
        return f"{self.title}: {self.explanation} {self.next_action}".strip()


@dataclass(frozen=True)
class ReadinessIssue:
    key: str
    title: str
    explanation: str
    next_action: str
    required: bool = True


@dataclass(frozen=True)
class ProjectReadiness:
    ready: bool
    issues: tuple[ReadinessIssue, ...]
    pdf_count: int
    next_action: str


def default_dependency_check() -> tuple[list[str], list[str], list[str]]:
    return missing_python_dependencies(), check_system_tools(), check_r_packages()


def _has_api_key(project_dir: Path, explicit_key: str | None) -> bool:
    if (explicit_key or "").strip():
        return True

    load_dotenv(project_dir / ".env")
    load_dotenv(Path.cwd() / ".env")
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return True

    return load_saved_api_key() is not None


def check_project_readiness(
    project_dir: Path,
    *,
    api_key: str | None,
    model: str | None,
    dependency_check: DependencyCheck = default_dependency_check,
) -> ProjectReadiness:
    project_dir = project_dir.expanduser().resolve()
    issues: list[ReadinessIssue] = []
    pdf_count = 0

    if not project_dir.exists():
        issues.append(
            ReadinessIssue(
                "project_missing",
                "Project folder missing",
                "The selected project folder does not exist.",
                "Create the folder or choose an existing project folder.",
            )
        )
    else:
        sources_dir = project_dir / "sources"
        if not sources_dir.exists():
            issues.append(
                ReadinessIssue(
                    "sources_missing",
                    "Sources folder missing",
                    "The project needs a sources/ folder for PDF articles.",
                    "Choose a project folder that already contains sources/, or create sources/ there and add PDF files.",
                )
            )
        else:
            pdf_count = len([path for path in sources_dir.rglob("*.pdf") if path.is_file()])
            if pdf_count == 0:
                issues.append(
                    ReadinessIssue(
                        "sources_empty",
                        "No PDFs found",
                        "No PDF files were found in sources/.",
                        "Add PDFs to sources/ before running.",
                    )
                )

        schema_path = project_dir / "extraction_schema.yml"
        if not schema_path.exists() and not DEFAULT_SCHEMA.exists():
            issues.append(
                ReadinessIssue(
                    "schema_missing",
                    "Extraction schema missing",
                    "No extraction_schema.yml file is available, and the bundled default schema could not be found.",
                    "Create extraction_schema.yml before running.",
                )
            )

    if not _has_api_key(project_dir, api_key):
        issues.append(
            ReadinessIssue(
                "api_key_missing",
                "Gemini API key missing",
                "Auto Z-Curve needs a Gemini API key before it can read PDFs.",
                "Enter a Gemini API key or save one permanently.",
            )
        )

    if not (model or "").strip():
        issues.append(
            ReadinessIssue(
                "model_missing",
                "Gemini model missing",
                "No Gemini model is selected.",
                "Choose a Gemini model before running.",
            )
        )

    try:
        missing_python, missing_tools, missing_r = dependency_check()
    except Exception as exc:
        issues.append(
            ReadinessIssue(
                "preflight_failed",
                "Preflight check failed",
                "Auto Z-Curve could not check R, Quarto, and package dependencies.",
                "Open the log for details, then install the missing dependency and try again.",
            )
        )
        missing_python, missing_tools, missing_r = [], [], []
        preflight_error = exc
    else:
        preflight_error = None

    if missing_python:
        issues.append(
            ReadinessIssue(
                "python_deps_missing",
                "Python packages missing",
                "Some required Python packages are not installed: " + ", ".join(missing_python) + ".",
                "Install the Python dependencies, then reopen Auto Z-Curve.",
            )
        )
    if "Quarto" in missing_tools:
        issues.append(
            ReadinessIssue(
                "quarto_missing",
                "Quarto missing",
                "Quarto is missing, so the report cannot be created.",
                "Install Quarto from https://quarto.org, then try again.",
            )
        )
    remaining_tools = [tool for tool in missing_tools if tool != "Quarto"]
    if remaining_tools:
        issues.append(
            ReadinessIssue(
                "system_tools_missing",
                "System tools missing",
                "Required system tools are missing: " + ", ".join(remaining_tools) + ".",
                "Install R and the missing tools, then try again.",
            )
        )
    if missing_r:
        issues.append(
            ReadinessIssue(
                "r_deps_missing",
                "R packages missing",
                "Some required R packages are not installed: " + ", ".join(missing_r) + ".",
                "Install the R packages, then try again.",
            )
        )

    if preflight_error is not None and issues:
        issues[-1] = ReadinessIssue(
            issues[-1].key,
            issues[-1].title,
            issues[-1].explanation,
            f"{issues[-1].next_action} Technical detail: {preflight_error}",
        )

    next_action = issues[0].next_action if issues else "Ready to run extraction."
    return ProjectReadiness(ready=not issues, issues=tuple(issues), pdf_count=pdf_count, next_action=next_action)


def classify_error(error: BaseException | str) -> UserFacingError:
    detail = str(error)
    text = detail.lower()

    if "gemini_api_key" in text or "api key" in text and ("required" in text or "missing" in text):
        return UserFacingError(
            "Gemini API key missing",
            "Auto Z-Curve cannot contact Gemini without an API key.",
            "Enter a Gemini API key or save one permanently.",
            detail,
        )
    if "api key not valid" in text or "invalid api key" in text or "permission_denied" in text or "unauthenticated" in text:
        return UserFacingError(
            "Gemini rejected the API key",
            "The saved or entered Gemini API key was not accepted.",
            "Check the key in Google AI Studio, then paste it again.",
            detail,
        )
    if "file exceeds max_upload_size_mb" in text or "too large" in text or "maximum size" in text:
        return UserFacingError(
            "PDF too large",
            "This PDF is too large to upload.",
            "Use a smaller PDF or raise the upload limit in the app configuration.",
            detail,
        )
    if "missing python packages" in text or "missing r packages" in text or "missing system tools" in text:
        if "quarto" in text:
            return UserFacingError(
                "Quarto missing",
                "Quarto is missing, so the report cannot be created.",
                "Install Quarto from https://quarto.org, then try again.",
                detail,
            )
        return UserFacingError(
            "Missing dependency",
            "Auto Z-Curve needs another program or package before it can run.",
            "Install the missing dependency listed in the log, then try again.",
            detail,
        )
    if "quarto is not installed" in text or "quarto is missing" in text:
        return UserFacingError(
            "Quarto missing",
            "Quarto is missing, so the report cannot be created.",
            "Install Quarto from https://quarto.org, then try again.",
            detail,
        )
    if "quarto" in text or "report rendering" in text or "report.html" in text:
        return UserFacingError(
            "Report could not be created",
            "The extraction may have finished, but the HTML report was not created.",
            "Install or repair Quarto, then retry report creation by running the project again.",
            detail,
        )
    if "extraction schema" in text or "schema" in text and ("yaml" in text or "json" in text or "required" in text):
        return UserFacingError(
            "Extraction schema problem",
            "The extraction_schema.yml file could not be read or used.",
            "Open extraction_schema.yml, fix the schema, then run again.",
            detail,
        )
    if "google-genai" in text or "gemini" in text or "deadline" in text or "timeout" in text or "network" in text:
        return UserFacingError(
            "Gemini request failed",
            "Gemini or the network did not complete the request.",
            "Check the internet connection and API key, then retry the failed PDF.",
            detail,
        )
    if "no effect rows" in text or "no usable" in text or "usable for z-curve" in text:
        return UserFacingError(
            "No usable statistics found",
            "The PDFs were processed, but no statistics could be used for z-curve.",
            "Review the report and extraction schema to decide whether the target effects need adjustment.",
            detail,
        )

    return UserFacingError(
        "Run failed",
        "Auto Z-Curve could not finish the requested action.",
        "Read the log for details, fix the issue, then try again.",
        detail,
    )


def failed_pdf_rows(project_dir: Path, limit: int = 8) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for record in load_extractions(project_dir):
        if record.get("status") == "ok":
            continue
        source = str(record.get("source_name") or "")
        summary = classify_error(str(record.get("error") or "")).compact()
        rows.append((source, summary))
    return rows[:limit]


def format_run_result(summary: RunSummary, project_dir: Path) -> str:
    report = "not rendered"
    if summary.report_path:
        try:
            report = str(summary.report_path.resolve().relative_to(project_dir.resolve()))
        except ValueError:
            report = str(summary.report_path)
    lines = [
        f"Report: {report}",
        f"Successful PDFs: {summary.successful_pdfs}",
        f"Failed PDFs: {summary.failed_pdfs}",
        f"Extracted effects: {summary.extracted_effects}",
        f"Usable z-curve inputs: {summary.usable_zcurve_inputs}",
        f"Input tokens: {summary.input_tokens}",
        f"Output tokens: {summary.output_tokens}",
        f"Total tokens: {summary.total_tokens}",
    ]
    return "\n".join(lines)


def report_opener_command(report_path: Path, system: str | None = None) -> Sequence[str] | None:
    system = system or platform.system()
    if system == "Darwin":
        return ["open", str(report_path)]
    if system == "Windows":
        return None
    return ["xdg-open", str(report_path)]


def open_report_path(
    report_path: Path,
    *,
    system: str | None = None,
    popen: Callable[..., object] = subprocess.Popen,
    startfile: Callable[[str], object] | None = None,
) -> None:
    report_path = report_path.expanduser().resolve()
    if not report_path.exists():
        raise FileNotFoundError(f"Report does not exist: {report_path}")

    current_system = system or platform.system()
    if current_system == "Windows":
        opener = startfile or getattr(os, "startfile", None)
        if opener is None:
            raise RuntimeError("No Windows report opener is available.")
        opener(str(report_path))
        return

    command = report_opener_command(report_path, current_system)
    if command is None:
        raise RuntimeError("No report opener is available for this platform.")
    popen(command)
