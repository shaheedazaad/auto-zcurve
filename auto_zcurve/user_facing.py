from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .artifacts import load_extractions
from .llm import STATUS_NEEDS_REVIEW, record_review_reasons
from .runner import RunSummary


@dataclass(frozen=True)
class UserFacingError:
    title: str
    explanation: str
    next_action: str
    technical_detail: str | None = None

    def compact(self) -> str:
        return f"{self.title}: {self.explanation} {self.next_action}".strip()


def classify_error(error: BaseException | str) -> UserFacingError:
    detail = str(error)
    text = detail.lower()

    if "openrouter_api_key" in text or "openrouter" in text and "api key" in text and ("required" in text or "missing" in text):
        return UserFacingError(
            "OpenRouter API key missing",
            "auto-zcurve cannot contact OpenRouter without an API key.",
            "Enter an OpenRouter API key or save one permanently.",
            detail,
        )
    if "gemini_api_key" in text or "api key" in text and ("required" in text or "missing" in text):
        return UserFacingError(
            "Gemini API key missing",
            "auto-zcurve cannot contact Gemini without an API key.",
            "Enter a Gemini API key or save one permanently.",
            detail,
        )
    if "openrouter" in text and ("401" in text or "403" in text or "invalid api key" in text):
        return UserFacingError(
            "OpenRouter rejected the API key",
            "The saved or entered OpenRouter API key was not accepted.",
            "Check the key in OpenRouter, then paste it again.",
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
            "auto-zcurve needs another program or package before it can run.",
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
    if "openrouter" in text:
        return UserFacingError(
            "OpenRouter request failed",
            "OpenRouter or the selected model did not complete the request.",
            "Check the model compatibility, internet connection, and API key, then retry the failed PDF.",
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
        "auto-zcurve could not finish the requested action.",
        "Read the log for details, fix the issue, then try again.",
        detail,
    )


def review_reasons_text(record: dict) -> str:
    reasons = record_review_reasons(record)
    return "; ".join(reasons) if reasons else "no reason was recorded"


def retry_reason(record: dict) -> str:
    """Explain why a failed or needs-review extraction is offered for retry."""
    if record.get("status") == STATUS_NEEDS_REVIEW:
        reason = f"Needs review: {review_reasons_text(record)}"
        if record.get("retry_error"):
            reason += f"; last retry failed: {classify_error(str(record['retry_error'])).compact()}"
        return reason
    return classify_error(str(record.get("error") or "")).compact()


def needs_review_pdf_rows(project_dir: Path) -> list[tuple[str, str]]:
    return [
        (str(record.get("source_name") or ""), review_reasons_text(record))
        for record in load_extractions(project_dir)
        if record.get("status") == STATUS_NEEDS_REVIEW
    ]


def format_run_result(summary: RunSummary, project_dir: Path) -> str:
    report = "not rendered"
    if summary.report_path:
        try:
            report = summary.report_path.resolve().relative_to(project_dir.resolve()).as_posix()
        except ValueError:
            report = summary.report_path.as_posix()
    lines = [
        f"Report: {report}",
        f"Successful PDFs: {summary.successful_pdfs}",
        f"Needs review PDFs: {summary.needs_review_pdfs}",
        f"Failed PDFs: {summary.failed_pdfs}",
        f"Extracted effects: {summary.extracted_effects}",
        f"Usable z-curve inputs: {summary.usable_zcurve_inputs}",
        f"Input tokens: {summary.input_tokens}",
        f"Output tokens: {summary.output_tokens}",
        f"Total tokens: {summary.total_tokens}",
    ]
    review_rows = needs_review_pdf_rows(project_dir)
    if review_rows:
        lines.append("Needs review:")
        lines.extend(f"  {source}: {reasons}" for source, reasons in review_rows)
    return "\n".join(lines)
