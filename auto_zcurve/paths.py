from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = REPO_ROOT / "config" / "extraction_schema.yml"
DEFAULT_INSTRUCTIONS = REPO_ROOT / "config" / "statistic_extraction_instructions.md"
REPORT_TEMPLATE = REPO_ROOT / "report" / "report_template.qmd"
R_PREFLIGHT_SCRIPT = REPO_ROOT / "scripts" / "preflight.R"
