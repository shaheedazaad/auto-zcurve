"""Spreadsheet-safe copies of the canonical CSV outputs.

Excel and LibreOffice treat a cell that starts with ``=``, ``+``, ``-`` or
``@`` (or a tab or carriage return, even after leading spaces) as a formula.
The canonical CSV files stay untouched for the R report and for scripts; these
helpers build a separate copy that is safe to open in a spreadsheet application.
"""

from __future__ import annotations

import csv
import io
import re

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
SIGNED_NUMBER = re.compile(r" *[+-](?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)? *")
UTF8_BOM = "﻿"
MAX_FIELD_SIZE = 16 * 1024 * 1024

SPREADSHEET_SAFE_FILES = {
    "disclosure_table.csv": "Disclosure table",
    "run_log.csv": "Run log",
    "zcurve_reproduction_settings.csv": "Reproduction settings",
}


def spreadsheet_safe_name(name: str) -> str:
    return name.removesuffix(".csv") + ".spreadsheet.csv"


def escape_cell(value: str) -> str:
    """Prefix formula-like cells with an apostrophe; leave signed numbers numeric."""
    if not value.lstrip(" ").startswith(FORMULA_PREFIXES):
        return value
    if SIGNED_NUMBER.fullmatch(value):
        return value
    return "'" + value


def spreadsheet_safe_csv(text: str) -> bytes:
    """Return a UTF-8 (with BOM), CRLF copy of ``text`` with formula-like cells escaped."""
    text = text.removeprefix(UTF8_BOM)
    previous_limit = csv.field_size_limit()
    try:
        csv.field_size_limit(max(previous_limit, MAX_FIELD_SIZE))
        rows = list(csv.reader(io.StringIO(text, newline="")))
    finally:
        csv.field_size_limit(previous_limit)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in rows:
        writer.writerow([escape_cell(cell) for cell in row])
    return (UTF8_BOM + buffer.getvalue()).encode("utf-8")
