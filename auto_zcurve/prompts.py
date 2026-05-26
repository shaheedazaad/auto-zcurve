from __future__ import annotations

from pathlib import Path

from .schema import ExtractionSchema, build_role_lookup


def default_effect_definition() -> str:
    return (
        "Extract each article's 'focal' effects. Focal effects are those that support "
        "the claims in either the title or abstract of the article (a non-focal effect, "
        "for example, would be a manipulation check)."
    )


def render_text_template(text: str, values: dict[str, str]) -> str:
    out = text
    for name, value in values.items():
        out = out.replace(f"{{{{{name}}}}}", value)
    return out


def build_system_prompt(
    config: ExtractionSchema,
    instruction_path: Path,
    effect_definition: str | None = None,
) -> str:
    lookup = build_role_lookup(config)
    reported_field = (
        lookup["effect"].get("reported_statistic")
        or lookup["effect"].get("reported_test")
        or "reported_statistic"
    )
    with instruction_path.open("r", encoding="utf-8") as handle:
        base_prompt = render_text_template(
            handle.read(),
            {"reported_statistic_field": reported_field},
        )

    definition = (effect_definition or default_effect_definition()).strip()
    if not definition:
        return base_prompt
    return f"{base_prompt}\n\n## Effects of interest\n{definition}"
