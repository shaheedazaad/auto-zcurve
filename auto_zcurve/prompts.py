from __future__ import annotations

from pathlib import Path

from .schema import ExtractionSchema, build_role_lookup


def render_text_template(text: str, values: dict[str, str]) -> str:
    out = text
    for name, value in values.items():
        out = out.replace(f"{{{{{name}}}}}", value)
    return out


XML_EVIDENCE_NOTE = """

## Source format: XML

The article below is the publisher's XML file, not a PDF, so it has no page numbers. Wherever these instructions or the schema ask for a page number, cite where the text sits in the article instead: the section heading path, for example (Results › Study 2), or for tables the table label, row and column, for example (Table 3, row "Extraversion", column "r"). Quote the article's words, not its XML markup."""


def build_system_prompt(
    config: ExtractionSchema,
    instruction_path: Path,
    source_format: str = "pdf",
) -> str:
    lookup = build_role_lookup(config)
    reported_field = (
        lookup["effect"].get("reported_statistic")
        or lookup["effect"].get("reported_test")
        or "reported_statistic"
    )
    with instruction_path.open("r", encoding="utf-8") as handle:
        prompt = render_text_template(
            handle.read(),
            {
                "reported_statistic_field": reported_field,
                "eligible_field": lookup["effect"].get("eligible") or "eligible",
                "eligibility_explanation_field": lookup["effect"].get("eligibility_explanation") or "eligibility_explanation",
            },
        )
    return prompt + XML_EVIDENCE_NOTE if source_format == "xml" else prompt
