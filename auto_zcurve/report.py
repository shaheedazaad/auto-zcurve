from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .paths import DEFAULT_INSTRUCTIONS, REPORT_TEMPLATE, REPO_ROOT


def _current_r_libs() -> str | None:
    rscript = shutil.which("Rscript")
    if rscript is None:
        return None
    completed = subprocess.run(
        [rscript, "-e", "cat(paste(.libPaths(), collapse=.Platform$path.sep))"],
        check=False,
        text=True,
        capture_output=True,
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
    quarto = shutil.which("quarto")
    if quarto is None:
        raise RuntimeError("Quarto is not installed or not on PATH.")

    out_dir = project_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    render_template = out_dir / "_auto_zcurve_report.qmd"
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
            "AUTO_ZCURVE_EFFECT_DEFINITION": effect_definition or "",
            "DENO_DIR": str(cache_dir / "deno"),
            "QUARTO_CACHE": str(cache_dir / "quarto"),
            "XDG_CACHE_HOME": str(cache_dir / "xdg"),
            "HOME": str(cache_dir / "home"),
            "USERPROFILE": str(cache_dir / "home"),
        }
    )
    if r_libs:
        env["R_LIBS"] = r_libs
    completed = subprocess.run(
        [
            quarto,
            "render",
            render_template.name,
            "--to",
            "html",
            "--output",
            expected.name,
        ],
        check=False,
        text=True,
        capture_output=True,
        env=env,
        cwd=str(out_dir),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Quarto report rendering failed.\n"
            + (completed.stdout or "")
            + (completed.stderr or "")
        )
    if expected.exists():
        render_template.unlink(missing_ok=True)
        return expected

    raise RuntimeError(f"Quarto completed but did not create {expected}.")
