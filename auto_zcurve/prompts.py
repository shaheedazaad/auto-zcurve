from __future__ import annotations

from pathlib import Path

from .schema import ExtractionSchema, build_role_lookup


def render_text_template(text: str, values: dict[str, str]) -> str:
    out = text
    for name, value in values.items():
        out = out.replace(f"{{{{{name}}}}}", value)
    return out


def build_system_prompt(
    config: ExtractionSchema,
    instruction_path: Path,
) -> str:
    lookup = build_role_lookup(config)
    reported_field = (
        lookup["effect"].get("reported_statistic")
        or lookup["effect"].get("reported_test")
        or "reported_statistic"
    )
    with instruction_path.open("r", encoding="utf-8") as handle:
        return render_text_template(
            handle.read(),
            {
                "reported_statistic_field": reported_field,
            },
        )
