"""The needs_review extraction status, end to end (issue #1)."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from auto_zcurve import cli, gemini
from auto_zcurve import runner as r
from auto_zcurve import user_facing as u
from auto_zcurve.artifacts import (
    load_extractions,
    load_run_log,
    run_log_csv_path,
    save_extractions,
    upsert_extraction,
)
from auto_zcurve.config import RunSettings
from auto_zcurve.llm import (
    ExtractionResult,
    finish_reason_review_reason,
    record_review_reasons,
    review_status,
)
from auto_zcurve.models import ModelOption
from auto_zcurve.projects import create_project, project_snapshot
from auto_zcurve.web import create_app


class FinishReason(Enum):
    STOP = 1
    MAX_TOKENS = 2


def gemini_response(text: str, *finish_reasons: object) -> NS:
    candidates = [NS(content=NS(parts=[NS(text=text)]), finish_reason=reason) for reason in finish_reasons]
    if not candidates:
        candidates = [NS(content=NS(parts=[NS(text=text)]))]
    return NS(candidates=candidates, usage_metadata=NS(prompt_token_count=1, candidates_token_count=1, total_token_count=2))


class Console(Mock):
    @contextmanager
    def progress(self, *args):
        yield Mock()


class StatusHelperTests(unittest.TestCase):
    def test_finish_reasons_and_status(self):
        self.assertIsNone(finish_reason_review_reason(None))
        self.assertIsNone(finish_reason_review_reason(" "))
        self.assertIsNone(finish_reason_review_reason("stop"))
        self.assertEqual(finish_reason_review_reason("MAX_TOKENS"), "finish reason: MAX_TOKENS")
        self.assertIsNone(finish_reason_review_reason("end_turn", normal=("end_turn",)))
        self.assertEqual(review_status([]), "ok")
        self.assertEqual(review_status(["JSON was repaired"]), "needs_review")

    def test_saved_reasons_tolerate_legacy_and_malformed_records(self):
        self.assertEqual(record_review_reasons({}), [])
        self.assertEqual(record_review_reasons({"review_reasons": "JSON was repaired"}), [])
        self.assertEqual(record_review_reasons({"review_reasons": ["a", " ", 3]}), ["a", "3"])


class GeminiReviewTests(unittest.TestCase):
    def extract(self, response: NS) -> ExtractionResult:
        client = Mock()
        client.models.generate_content.return_value = response
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.pdf"
            source.write_bytes(b"%PDF")
            with patch("google.genai.Client", return_value=client), patch.object(gemini, "build_system_prompt", return_value="prompt"), patch.object(gemini, "validate_extracted_json"):
                return gemini.extract_pdf(source_path=source, source_name="a.pdf", api_key="key", primary_model="gemini-test", response_schema={}, schema_config=object(), instruction_path=Path(tmp) / "i.md")

    def test_repaired_json_needs_review_and_keeps_values(self):
        result = self.extract(gemini_response('{"effects": [{"claim": "x"}]', "STOP"))
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.review_reasons, ("JSON was repaired",))
        self.assertEqual(result.data, {"effects": [{"claim": "x"}]})
        self.assertEqual(result.effect_count, 1)

    def test_abnormal_finish_reason_needs_review_even_when_json_is_valid(self):
        for reason, expected in [("MAX_TOKENS", "MAX_TOKENS"), (FinishReason.MAX_TOKENS, "MAX_TOKENS"), ("SAFETY", "SAFETY"), ("RECITATION", "RECITATION"), ("FinishReason.OTHER", "OTHER")]:
            with self.subTest(reason=reason):
                result = self.extract(gemini_response('{"effects": []}', reason))
                self.assertEqual(result.status, "needs_review")
                self.assertEqual(result.review_reasons, (f"finish reason: {expected}",))
                self.assertEqual(result.data, {"effects": []})

    def test_repair_and_finish_reasons_are_all_listed_once(self):
        result = self.extract(gemini_response('{"effects": [', "MAX_TOKENS", "MAX_TOKENS", "STOP"))
        self.assertEqual(result.review_reasons, ("JSON was repaired", "finish reason: MAX_TOKENS"))

    def test_normal_or_missing_finish_reason_is_ok(self):
        for reasons in [("STOP",), (FinishReason.STOP,), (None,), ("FINISH_REASON_UNSPECIFIED",), ()]:
            with self.subTest(reasons=reasons):
                result = self.extract(gemini_response('{"effects": []}', *reasons))
                self.assertEqual((result.status, result.review_reasons), ("ok", ()))

    def test_finish_reasons_are_read_from_rest_style_dicts(self):
        self.assertEqual(gemini._response_finish_reasons({"candidates": [{"finishReason": "MAX_TOKENS"}, {}]}), ["MAX_TOKENS"])
        self.assertEqual(gemini._response_finish_reasons(NS()), [])


class ArtifactAndRunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name)
        (self.project / "sources").mkdir()
        (self.project / "extraction_schema.yml").write_text("effects: {claim: {type: string}}")
        for name in ["ok.pdf", "review.pdf", "failed.pdf", "new.pdf"]:
            (self.project / "sources" / name).write_bytes(b"%PDF")
        self.settings = RunSettings(primary_model="test", request_delay_sec=0)
        self.option = ModelOption("test", "Test")
        self.console = Console()

    def saved_states(self):
        save_extractions(self.project, [
            {"source_name": "ok.pdf", "status": "ok", "effects": 2},
            {"source_name": "review.pdf", "status": "needs_review", "effects": 3, "review_reasons": ["finish reason: MAX_TOKENS"]},
            {"source_name": "failed.pdf", "status": "error", "error": "network timeout"},
        ])

    def reviewed(self, **kwargs):
        return ExtractionResult(kwargs["source_path"], kwargs["source_name"], "needs_review", data={"effects": [{"claim": "x"}]}, raw_response="{", repaired_response="{}", review_reasons=("JSON was repaired",))

    def test_status_and_reasons_are_saved_in_record_and_run_log(self):
        with patch.object(r, "extract_pdf", side_effect=self.reviewed):
            r._process_one(project_dir=self.project, source_path=self.project / "sources/review.pdf", settings=self.settings, api_key="key", retry=False, run_id="run", model_option=self.option)
        record = load_extractions(self.project)[0]
        self.assertEqual((record["status"], record["review_reasons"], record["effects"], record["json_repaired"]), ("needs_review", ["JSON was repaired"], 1, True))
        saved = json.loads(next((self.project / "output/raw").glob("review-*.json")).read_text())
        self.assertEqual(saved["review_reasons"], ["JSON was repaired"])
        log = load_run_log(self.project)[0]
        self.assertEqual((log["status"], log["review_reasons"]), ("needs_review", ["JSON was repaired"]))
        with run_log_csv_path(self.project).open(newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual((row["status"], row["review_reasons"]), ("needs_review", "JSON was repaired"))

    def test_plain_run_skips_needs_review_and_retry_includes_it(self):
        self.saved_states()
        with patch.object(r, "validate_model_option", return_value=self.option), patch.object(r, "load_run_settings", return_value=None), patch.object(r, "_process_paths") as process:
            r.run_project(project_dir=self.project, settings=self.settings, assume_yes=True, interactive=False, force=False, skip_report=True, console=self.console, api_key="key")
            self.assertEqual(sorted(path.name for path in process.call_args.kwargs["paths"]), ["failed.pdf", "new.pdf"])
            r.retry_project(project_dir=self.project, settings=self.settings, selected_sources=None, assume_yes=True, skip_report=True, console=self.console, api_key="key")
            self.assertEqual(sorted(path.name for path in process.call_args.kwargs["paths"]), ["failed.pdf", "review.pdf"])

    def test_summary_counts_needs_review_separately(self):
        self.saved_states()
        summary = r.summarize(self.project, None)
        self.assertEqual((summary.successful_pdfs, summary.needs_review_pdfs, summary.failed_pdfs, summary.extracted_effects), (1, 1, 1, 5))

    def test_legacy_records_without_review_fields_behave_as_before(self):
        save_extractions(self.project, [
            {"source_name": "ok.pdf", "status": "ok", "effects": 2, "json_repaired": True},
            {"source_name": "failed.pdf", "status": "error"},
        ])
        summary = r.summarize(self.project, None)
        self.assertEqual((summary.successful_pdfs, summary.needs_review_pdfs, summary.failed_pdfs), (1, 0, 1))
        self.assertEqual(u.needs_review_pdf_rows(self.project), [])
        article = {item["name"]: item for item in project_snapshot(NS(path=self.project, project_id="p", name="P", created_at=""))["articles"]}["ok.pdf"]
        self.assertEqual((article["status"], article["review_reasons"], article["json_repaired"]), ("ok", [], True))

    def test_user_facing_rows_and_run_result(self):
        self.saved_states()
        save_extractions(self.project, load_extractions(self.project) + [{"source_name": "unexplained.pdf", "status": "needs_review"}])
        self.assertEqual(u.failed_pdf_rows(self.project), [
            ("review.pdf", "Needs review: finish reason: MAX_TOKENS"),
            ("failed.pdf", u.classify_error("network timeout").compact()),
            ("unexplained.pdf", "Needs review: no reason was recorded"),
        ])
        text = u.format_run_result(r.summarize(self.project, None), self.project)
        self.assertIn("Needs review PDFs: 2", text)
        self.assertIn("Needs review:\n  review.pdf: finish reason: MAX_TOKENS\n  unexplained.pdf: no reason was recorded", text)

    def test_cli_summary_lists_reasons_and_offers_retry(self):
        self.saved_states()
        console = Mock()
        cli.print_summary(r.summarize(self.project, None), console, self.project)
        tables = {call.args[0]: call.args[2] for call in console.table.call_args_list}
        self.assertIn(("Needs review PDFs", 1), tables["Run Summary"])
        self.assertEqual(tables["PDFs Needing Review"], [("review.pdf", "finish reason: MAX_TOKENS")])

        console = Mock()
        args = NS(skip_report=True)
        with patch.object(cli, "_questionary", return_value=None), patch("builtins.input", return_value="y"), patch.object(cli, "retry_project") as retry:
            cli.offer_interactive_retry(args, self.project, self.settings, console, "key")
        retry.assert_called_once()
        rows = console.table.call_args.args[2]
        self.assertIn(("review.pdf", "Needs review: finish reason: MAX_TOKENS"), rows)

    def test_interactive_run_offers_retry_when_only_review_is_needed(self):
        summary = NS(failed_pdfs=0, needs_review_pdfs=1)
        args = cli.build_parser().parse_args(["run", str(self.project), "--skip-preflight"])
        with patch.object(cli, "run_preflight"), patch.object(cli, "prompt_api_key", return_value="key"), patch.object(cli, "_settings_from_args", return_value=self.settings), patch.object(cli, "run_project", return_value=summary), patch.object(cli, "print_summary"), patch.object(cli, "offer_interactive_retry", return_value=None) as offer:
            self.assertEqual(cli.run_command(args, self.project, interactive=True, console=self.console), 0)
        offer.assert_called_once()


class WebNeedsReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = TestClient(create_app(token="token", projects_root=self.root), base_url="http://127.0.0.1")
        self.addCleanup(self.client.close)

    def test_results_table_shows_status_reasons_and_enables_retry(self):
        project = create_project("Review", root=self.root)
        for name in ["clean.pdf", "review.pdf", "todo.pdf"]:
            (project.path / "sources" / name).write_bytes(b"%PDF")
        upsert_extraction(project.path, ExtractionResult(project.path / "sources/clean.pdf", "clean.pdf", "ok", data={"effects": []}))
        upsert_extraction(project.path, ExtractionResult(project.path / "sources/review.pdf", "review.pdf", "needs_review", data={"effects": [{}]}, review_reasons=("JSON was repaired", "finish reason: MAX_TOKENS")))
        snapshot = project_snapshot(project)
        self.assertEqual((snapshot["successful_count"], snapshot["needs_review_count"], snapshot["extracted_count"], snapshot["failed_count"], snapshot["remaining_count"]), (1, 1, 2, 0, 1))
        (project.path / "output/report.html").write_text("report")
        self.assertEqual(project_snapshot(project)["report_summary"]["needs_review_display"], "1")

        page = self.client.get(f"/token/projects/{project.project_id}").text
        self.assertIn('<span class="badge article-status" data-variant="warning">Needs review</span>', page)
        self.assertIn("Needs review: JSON was repaired; finish reason: MAX_TOKENS", page)
        self.assertIn("article-row-needs-review", page)
        self.assertIn("2 processed (1 need review)", page)
        self.assertIn("<strong>1</strong> need review", page)
        self.assertIn('data-has-failures="true"', page)
        self.assertIn("Continue processing 1 PDF ", page)

    def test_server_rendered_rows_show_parser_warnings_with_review_reasons(self):
        project = create_project("Warnings", root=self.root)
        for name in ["both.pdf", "reasons.pdf", "warning.pdf"]:
            (project.path / "sources" / name).write_bytes(b"%PDF")
        upsert_extraction(project.path, ExtractionResult(project.path / "sources/both.pdf", "both.pdf", "needs_review", data={"effects": []}, review_reasons=("JSON was repaired",), parser_warnings=("<b>page 3</b> dropped",)))
        upsert_extraction(project.path, ExtractionResult(project.path / "sources/reasons.pdf", "reasons.pdf", "needs_review", data={"effects": []}, review_reasons=("finish reason: MAX_TOKENS",)))
        upsert_extraction(project.path, ExtractionResult(project.path / "sources/warning.pdf", "warning.pdf", "ok", data={"effects": []}, parser_warnings=("table skipped",)))

        page = self.client.get(f"/token/projects/{project.project_id}").text
        rows = {name: page.split(f'title="{name}"', 1)[1].split("</tr>", 1)[0] for name in ["both.pdf", "reasons.pdf", "warning.pdf"]}
        self.assertIn("Needs review: JSON was repaired</small>", rows["both.pdf"])
        self.assertIn('<small class="article-message u-warning">&lt;b&gt;page 3&lt;/b&gt; dropped</small>', rows["both.pdf"])
        self.assertIn("Needs review: finish reason: MAX_TOKENS</small>", rows["reasons.pdf"])
        self.assertEqual(rows["reasons.pdf"].count("article-message"), 1)
        self.assertIn('<small class="article-message u-warning">table skipped</small>', rows["warning.pdf"])
        self.assertNotIn("Needs review", rows["warning.pdf"])
        self.assertIn("<span data-article-count>3 articles</span>", page)
        self.assertIn("<span data-processed-count>3 processed (2 need review)</span>", page)


if __name__ == "__main__":
    unittest.main()
