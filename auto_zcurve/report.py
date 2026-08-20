from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .paths import (
    DEFAULT_INSTRUCTIONS,
    REPORT_TEMPLATE,
    REPO_ROOT,
    REPRODUCIBLE_REPORT_TEMPLATE,
)


def _current_r_libs() -> str | None:
    rscript = shutil.which("Rscript")
    if rscript is None:
        return None
    env = os.environ.copy()
    conda_prefix = os.environ.get("CONDA_PREFIX", "").strip()
    if conda_prefix:
        managed_library = Path(conda_prefix) / "lib" / "R" / "library"
        if sys.platform == "win32":
            managed_library = Path(conda_prefix) / "Lib" / "R" / "library"
        env["R_LIBS_USER"] = str(managed_library)
        env["R_LIBS_SITE"] = str(managed_library)
    completed = subprocess.run(
        [rscript, "-e", "cat(paste(.libPaths(), collapse=.Platform$path.sep))"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def render_report(
    *,
    project_dir: Path,
    schema_path: Path,
    model_name: str,
    effect_definition: str | None,
    instruction_path: Path = DEFAULT_INSTRUCTIONS,
) -> Path:
    out_dir = project_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    render_log = out_dir / "report_render.log"
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    quarto = shutil.which("quarto")
    if quarto is None:
        render_log.write_text(
            f"started_at: {started_at}\nstatus: not_started\nerror: Quarto is not installed or not on PATH.\n",
            encoding="utf-8",
        )
        raise RuntimeError(f"Quarto is not installed or not on PATH. Detailed log: {render_log}")

    render_template = out_dir / "report.qmd"
    shutil.copyfile(REPORT_TEMPLATE, render_template)
    expected = out_dir / "report.html"
    expected.unlink(missing_ok=True)
    cache_dir = out_dir / ".quarto-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "home").mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    r_libs = _current_r_libs()
    env.update(
        {
            "AUTO_ZCURVE_PROJECT_DIR": str(project_dir.resolve()),
            "AUTO_ZCURVE_OUTPUT_DIR": str(out_dir.resolve()),
            "AUTO_ZCURVE_REPO_DIR": str(REPO_ROOT.resolve()),
            "AUTO_ZCURVE_SCHEMA_PATH": str(schema_path.resolve()),
            "AUTO_ZCURVE_INSTRUCTIONS_PATH": str(instruction_path.resolve()),
            "AUTO_ZCURVE_MODEL_NAME": model_name,
            "AUTO_ZCURVE_VERSION": __version__,
            "AUTO_ZCURVE_EFFECT_DEFINITION": effect_definition or "",
            "DENO_DIR": str(cache_dir / "deno"),
            "QUARTO_CACHE": str(cache_dir / "quarto"),
            "XDG_CACHE_HOME": str(cache_dir / "xdg"),
            "HOME": str(cache_dir / "home"),
            "USERPROFILE": str(cache_dir / "home"),
        }
    )
    rscript = shutil.which("Rscript")
    if rscript:
        env["QUARTO_R"] = str(Path(rscript).resolve().parent)
    if r_libs:
        env["R_LIBS"] = r_libs
    command = [
        quarto,
        "render",
        render_template.name,
        "--to",
        "html",
        "--output",
        expected.name,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            env=env,
            cwd=str(out_dir),
        )
    except OSError as exc:
        render_log.write_text(
            f"started_at: {started_at}\nstatus: launch_failed\ncommand: {command!r}\nerror: {exc}\n",
            encoding="utf-8",
        )
        raise RuntimeError(f"Could not launch Quarto: {exc}. Detailed log: {render_log}") from exc
    render_log.write_text(
        "\n".join(
            [
                f"started_at: {started_at}",
                f"status: {'ok' if completed.returncode == 0 and expected.exists() else 'failed'}",
                f"returncode: {completed.returncode}",
                f"command: {command!r}",
                "\n--- stdout ---",
                completed.stdout or "(empty)",
                "\n--- stderr ---",
                completed.stderr or "(empty)",
                f"\nexpected_html: {expected}",
                f"html_created: {expected.exists()}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Quarto report rendering failed.\n"
            + f"Detailed log: {render_log}\n"
            + (completed.stdout or "")
            + (completed.stderr or "")
        )
    if expected.exists():
        shutil.copyfile(REPRODUCIBLE_REPORT_TEMPLATE, render_template)
        return expected

    raise RuntimeError(
        f"Quarto completed but did not create {expected}. Detailed log: {render_log}\n"
        + (completed.stdout or "")
        + (completed.stderr or "")
    )
