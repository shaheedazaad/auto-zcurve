from __future__ import annotations

import sys
from pathlib import Path


_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_INSTALLED_ROOT = Path(sys.prefix) / "share" / "auto-zcurve"
REPO_ROOT = _SOURCE_ROOT if (_SOURCE_ROOT / "config" / "extraction_schema.yml").exists() else _INSTALLED_ROOT
DEFAULT_SCHEMA = REPO_ROOT / "config" / "extraction_schema.yml"
DEFAULT_INSTRUCTIONS = REPO_ROOT / "config" / "statistic_extraction_instructions.md"
REPORT_TEMPLATE = REPO_ROOT / "report" / "report_template.qmd"
REPRODUCIBLE_REPORT_TEMPLATE = REPO_ROOT / "report" / "reproducible_report.qmd"
R_PREFLIGHT_SCRIPT = REPO_ROOT / "scripts" / "preflight.R"
MODEL_CONFIG = REPO_ROOT / "models.yml"
