from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .console import CliConsole
from .paths import R_PREFLIGHT_SCRIPT, REPO_ROOT


PYTHON_DEPS = {
    "google.genai": "google-genai",
    "yaml": "PyYAML",
    "rich": "rich",
    "questionary": "questionary",
}


class PreflightError(RuntimeError):
    pass


def missing_python_dependencies() -> list[str]:
    missing: list[str] = []
    for module, package in PYTHON_DEPS.items():
        if importlib.util.find_spec(module) is None:
            missing.append(package)
    return missing


def _prompt_confirm(message: str, default: bool = False) -> bool:
    try:
        import questionary

        return bool(questionary.confirm(message, default=default).ask())
    except Exception:
        suffix = "Y/n" if default else "y/N"
        answer = input(f"{message} [{suffix}] ").strip().lower()
        if not answer:
            return default
        return answer in {"y", "yes"}


def ensure_python_deps(project_dir: Path, interactive: bool, console: CliConsole) -> None:
    missing = missing_python_dependencies()
    if not missing:
        return

    message = "Missing Python packages: " + ", ".join(missing)
    if not interactive:
        raise PreflightError(
            f"{message}. Install the CLI dependencies or rerun interactively to create a project-local virtualenv."
        )

    console.warn(message)
    if not _prompt_confirm(f"Create/update {project_dir / '.venv'} and install CLI dependencies there?", default=True):
        raise PreflightError("Python dependency preflight was not completed.")

    venv_dir = project_dir / ".venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    pip = venv_dir / ("Scripts/pip.exe" if sys.platform == "win32" else "bin/pip")
    subprocess.run([str(pip), "install", "-e", str(REPO_ROOT)], check=True)
    raise PreflightError(
        f"Virtualenv is ready at {venv_dir}. Activate it and rerun auto-zcurve from that environment."
    )


def check_system_tools() -> list[str]:
    missing = []
    if shutil.which("R") is None and shutil.which("Rscript") is None:
        missing.append("R")
    if shutil.which("quarto") is None:
        missing.append("Quarto")
    return missing


def check_r_packages() -> list[str]:
    if shutil.which("Rscript") is None:
        return ["Rscript"]
    completed = subprocess.run(
        ["Rscript", str(R_PREFLIGHT_SCRIPT)],
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        try:
            payload = json.loads(completed.stdout.strip() or "{}")
            return list(payload.get("missing") or [])
        except json.JSONDecodeError:
            return ["unknown R package preflight failure"]
    return []


def ensure_r_deps(project_dir: Path, interactive: bool, console: CliConsole) -> None:
    missing_tools = check_system_tools()
    if missing_tools:
        raise PreflightError(
            "Missing system tools: "
            + ", ".join(missing_tools)
            + ". Install R from https://cran.r-project.org and Quarto from https://quarto.org."
        )

    missing_packages = check_r_packages()
    if not missing_packages:
        return

    message = "Missing R packages: " + ", ".join(missing_packages)
    if not interactive:
        raise PreflightError(
            f"{message}. Install them in a project-local renv library or rerun interactively."
        )

    console.warn(message)
    if not _prompt_confirm(f"Restore/install R packages into project-local renv for {project_dir}?", default=False):
        raise PreflightError("R package preflight was not completed.")

    command = (
        "if (!requireNamespace('renv', quietly = TRUE)) install.packages('renv'); "
        "renv::init(project = commandArgs(TRUE)[[1]], bare = TRUE, restart = FALSE); "
        "install.packages(commandArgs(TRUE)[-1])"
    )
    subprocess.run(["Rscript", "-e", command, str(project_dir), *missing_packages], check=True)


def run_preflight(project_dir: Path, interactive: bool, console: CliConsole) -> None:
    ensure_python_deps(project_dir, interactive, console)
    ensure_r_deps(project_dir, interactive, console)
