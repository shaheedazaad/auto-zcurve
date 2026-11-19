"""XML sources: accepted alongside PDFs and sent to the model as article text."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from auto_zcurve import gemini, openai_compatible, openrouter, providers
from auto_zcurve import runner as r
from auto_zcurve import web as w
from auto_zcurve.artifacts import load_extractions, load_run_log, save_extractions
from auto_zcurve.config import AppSettings, RunSettings
from auto_zcurve.llm import ExtractionResult
from auto_zcurve.models import ModelOption, resolve_input_mode
from auto_zcurve.paths import DEFAULT_INSTRUCTIONS
from auto_zcurve.projects import ProjectError, create_project, project_snapshot, safe_upload_name, save_upload
from auto_zcurve.prompts import XML_EVIDENCE_NOTE, build_system_prompt, render_text_template
from auto_zcurve.schema import build_response_schema, parse_extraction_schema
from auto_zcurve.user_facing import classify_error
from auto_zcurve.sources import (
    XML_TEXT_INPUT_MODE,
    SourceError,
    check_context_length,
    check_xml_file,
    decode_xml,
    discover_sources,
    estimate_tokens,
)

# Small hand-written fixtures in the two real-world shapes.
ELSEVIER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<full-text-retrieval-response xmlns="http://www.elsevier.com/xml/svapi/article/dtd" xmlns:ce="http://www.elsevier.com/xml/common/dtd" xmlns:ja="http://www.elsevier.com/xml/ja/dtd">
  <originalText><ja:article><ja:body><ce:sections><ce:section>
    <ce:section-title>Results</ce:section-title>
    <ce:para>Extraversion predicted well-being, r(98) = .32, p = .001.</ce:para>
  </ce:section></ce:sections></ja:body></ja:article></originalText>
</full-text-retrieval-response>
"""
JATS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.1 20151215//EN" "https://jats.nlm.nih.gov/publishing/1.1/JATS-journalpublishing1.dtd">
<article article-type="research-article"><body><sec><title>Study 2</title>
  <p>The effect replicated, t(45) = 2.10, p = .041.</p>
</sec></body></article>
"""
SCHEMA = "name: result\neffects:\n  claim:\n    type: string\n    required: true\n"


class SourceFormatTests(unittest.TestCase):
    def test_discovery_lists_pdf_and_xml_in_any_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "sources/nested").mkdir(parents=True)
            for name in ["a.pdf", "b.XML", "c.xml", "nested/d.xml", "notes.txt", "e.pdf.uploading"]:
                (project / "sources" / name).write_bytes(b"x")
            self.assertEqual(
                [p.relative_to(project / "sources").as_posix() for p in discover_sources(project)],
                ["a.pdf", "b.XML", "c.xml", "nested/d.xml"],
            )
            self.assertEqual([p.name for p in discover_sources(project, recursive=False)], ["a.pdf", "b.XML", "c.xml"])
            self.assertEqual(discover_sources(project / "missing"), [])

    def test_xml_validation_rejects_empty_binary_and_non_xml(self):
        for data, message in [
            (b"", "is empty"),
            (b"  \n", "is empty"),
            (b"\xff\xfe<\x00a\x00>\x00", "not UTF-8"),
            (b"<a>\x00</a>", "not a text file"),
            (b"%PDF-1.7", "does not look like XML"),
        ]:
            with self.subTest(data=data), self.assertRaisesRegex(SourceError, message):
                decode_xml(data, "a.xml")
        self.assertEqual(decode_xml("﻿  <a/>".encode("utf-8"), "a.xml"), "  <a/>")
        self.assertEqual(decode_xml(JATS_XML.encode(), "a.xml"), JATS_XML)

    def test_file_check_reads_in_chunks_and_matches_whole_file_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.xml"
            cases = [
                ("\ufeff" + " " * 5 + "<a>Müller — 研究</a>").encode("utf-8"),  # BOM, whitespace and multi-byte chars split across chunks
                b"     \n   ",
                b"    hello <a/>",
                b"<a>" + b"x" * 20 + b"\x00</a>",
                b"<a>" + b"x" * 20 + b"\xc3",
            ]
            for data in cases:
                path.write_bytes(data)
                with self.subTest(data=data):
                    try:
                        decode_xml(data, "a.xml")
                        expected = None
                    except SourceError as exc:
                        expected = str(exc)
                    try:
                        check_xml_file(path, "a.xml", chunk_size=3)
                        actual = None
                    except SourceError as exc:
                        actual = str(exc)
                    self.assertEqual(actual, expected)

    def test_legacy_pdf_hash_is_never_written_for_xml(self):
        xml = ExtractionResult(Path("a.xml"), "a.xml", "error", source_format="xml", source_sha256="x", source_pdf_sha256="legacy")
        self.assertIsNone(xml.pdf_sha256)
        self.assertEqual(ExtractionResult(Path("a.pdf"), "a.pdf", "ok", source_pdf_sha256="legacy").pdf_sha256, "legacy")
        self.assertEqual(ExtractionResult(Path("a.pdf"), "a.pdf", "ok", source_format="pdf", source_sha256="s").pdf_sha256, "s")

    def test_context_check_names_both_numbers_and_skips_unknown_limits(self):
        with self.assertRaises(SourceError) as caught:
            check_context_length(280_400, 128_000)
        self.assertEqual(str(caught.exception), "Article is ~280k tokens; this model accepts 128k.")
        shown = classify_error(caught.exception)
        self.assertEqual(shown.title, "Article too long for the model")
        self.assertIn("~280k tokens; this model accepts 128k", shown.compact())
        for limit in [None, 0, 280_400]:
            check_context_length(280_400, limit)
        self.assertEqual(estimate_tokens("x" * 300), 100)

    def test_every_provider_sends_xml_as_text(self):
        for provider in ["gemini", "openrouter", "openai_compatible"]:
            with self.subTest(provider=provider):
                self.assertEqual(resolve_input_mode(provider, ModelOption("m", "m"), "native", "xml"), XML_TEXT_INPUT_MODE)
        self.assertEqual(resolve_input_mode("openrouter", ModelOption("m", "m"), "native", "pdf"), "cloudflare_pdf")


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.schema = parse_extraction_schema(Path("config/extraction_schema.yml").read_text(encoding="utf-8"))

    def test_pdf_prompt_is_the_rendered_instructions_alone(self):
        prompt = build_system_prompt(self.schema, DEFAULT_INSTRUCTIONS)
        self.assertEqual(prompt, build_system_prompt(self.schema, DEFAULT_INSTRUCTIONS, "pdf"))
        self.assertNotIn("Source format: XML", prompt)
        self.assertNotIn("{{", prompt)
        self.assertEqual(prompt, render_text_template(DEFAULT_INSTRUCTIONS.read_text(encoding="utf-8"), {
            "reported_statistic_field": "reported_statistic",
            "eligible_field": "eligible",
            "eligibility_explanation_field": "eligibility_explanation",
        }))

    def test_xml_prompt_asks_for_section_and_table_locations(self):
        prompt = build_system_prompt(self.schema, DEFAULT_INSTRUCTIONS, "xml")
        self.assertEqual(prompt, build_system_prompt(self.schema, DEFAULT_INSTRUCTIONS) + XML_EVIDENCE_NOTE)
        self.assertIn("(Results › Study 2)", prompt)
        self.assertIn('(Table 3, row "Extraversion", column "r")', prompt)
        self.assertIn("no page numbers", prompt)


class ProviderRequestTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.config = parse_extraction_schema(SCHEMA)
        self.instructions = self.dir / "instructions.md"
        self.instructions.write_text("Extract the statistics.", encoding="utf-8")
        self.xml = self.dir / "article.xml"
        self.xml.write_text(ELSEVIER_XML, encoding="utf-8")

    def extract(self, provider: str, **kwargs):
        return providers.extract_document(**dict(dict(
            provider=provider, source_path=self.xml, source_name=self.xml.name, api_key="secret",
            primary_model="model", response_schema=build_response_schema(self.config), schema_config=self.config,
            instruction_path=self.instructions, request_timeout_sec=30, input_mode=XML_TEXT_INPUT_MODE,
        ), **kwargs))

    def test_gemini_sends_text_and_uploads_no_file(self):
        client = Mock()
        client.models.generate_content.return_value = NS(
            candidates=[NS(content=NS(parts=[NS(text='{"effects":[{"claim":"x"}]}')]), finish_reason="STOP")],
            usage_metadata=NS(prompt_token_count=9, candidates_token_count=2, total_token_count=11),
        )
        with patch("google.genai.Client", return_value=client):
            result = self.extract("gemini")
        client.files.upload.assert_not_called()
        prompt, document = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn(XML_EVIDENCE_NOTE.strip(), prompt)
        self.assertEqual(document, "Article text:\n" + ELSEVIER_XML)
        self.assertEqual((result.status, result.input_mode), ("ok", XML_TEXT_INPUT_MODE))
        self.assertEqual(result.estimated_input_tokens, estimate_tokens(ELSEVIER_XML))

    def test_openrouter_sends_one_text_part_without_parser_plugin(self):
        response = {"model": "vendor/model", "choices": [{"message": {"content": '{"effects":[{"claim":"x"}]}'}, "finish_reason": "stop"}]}
        with patch.object(openrouter, "_request_json", return_value=response) as request:
            result = self.extract("openrouter", context_length=128_000)
        payload = request.call_args.kwargs["payload"]
        content = payload["messages"][0]["content"]
        self.assertEqual([part["type"] for part in content], ["text"])
        self.assertTrue(content[0]["text"].endswith("\n\nArticle text:\n" + ELSEVIER_XML))
        self.assertIn(XML_EVIDENCE_NOTE.strip(), content[0]["text"])
        self.assertNotIn("plugins", payload)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("application/pdf", json.dumps(payload))
        self.assertEqual(result.input_mode, XML_TEXT_INPUT_MODE)

    def test_openai_compatible_sends_raw_xml_without_pdf_text_extraction(self):
        with patch.object(openai_compatible, "_call", return_value=('{"effects":[{"claim":"x"}]}', {}, "stop")) as call, \
                patch.object(openai_compatible, "pdf_text") as pdf_text, \
                patch("auto_zcurve.config.load_app_settings", return_value=AppSettings(openai_base_url="http://localhost:8000/v1")):
            result = self.extract("openai_compatible")
        pdf_text.assert_not_called()
        prompt = call.call_args.kwargs["prompt"]
        self.assertTrue(prompt.endswith("\n\nArticle text:\n" + ELSEVIER_XML))
        self.assertIn(XML_EVIDENCE_NOTE.strip(), prompt)
        self.assertEqual(result.input_mode, XML_TEXT_INPUT_MODE)

    def test_oversized_xml_fails_before_any_request(self):
        with patch.object(providers.importlib, "import_module") as load, \
                self.assertRaisesRegex(SourceError, r"Article is ~\d+ tokens; this model accepts 100\."):
            self.extract("openrouter", context_length=100)
        load.assert_not_called()

    def test_openrouter_text_mode_requires_article_text(self):
        with patch.object(openrouter, "_request_json") as request, self.assertRaisesRegex(RuntimeError, "needs the article text"):
            openrouter.extract_document(
                source_path=self.xml, source_name=self.xml.name, api_key="secret", primary_model="model",
                response_schema=build_response_schema(self.config), schema_config=self.config,
                instruction_path=self.instructions, input_mode=XML_TEXT_INPUT_MODE,
            )
        request.assert_not_called()

    def test_xml_is_never_sent_with_a_pdf_input_mode(self):
        with patch.object(providers.importlib, "import_module") as load, self.assertRaisesRegex(ValueError, "article text"):
            self.extract("openrouter", input_mode="cloudflare_pdf")
        load.assert_not_called()


class Console(Mock):
    @contextmanager
    def progress(self, *args):
        yield Mock()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name)
        (self.project / "sources").mkdir()
        (self.project / "extraction_schema.yml").write_text(SCHEMA)
        (self.project / "sources/a.pdf").write_bytes(b"%PDF-a")
        (self.project / "sources/b.xml").write_text(JATS_XML, encoding="utf-8")
        self.settings = RunSettings(provider="openrouter", primary_model="vendor/model", request_delay_sec=0)
        self.option = ModelOption("vendor/model", "Model", provider="openrouter", input_mode="cloudflare_pdf")
        self.calls: list[dict] = []

    def fake_extract(self, **kwargs):
        self.calls.append(kwargs)
        return ExtractionResult(kwargs["source_path"], kwargs["source_name"], "ok", data={"effects": [{"claim": "x"}]},
                                input_mode=kwargs["input_mode"])

    def run_extraction(self, retry=False):
        common = dict(project_dir=self.project, settings=self.settings, skip_report=True, console=Console(), api_key="secret", assume_yes=True)
        with patch.object(r, "validate_model_option", return_value=self.option), \
                patch.object(r, "extract_document", side_effect=self.fake_extract):
            if retry:
                return r.retry_project(selected_sources=None, **common)
            return r.run_project(interactive=False, force=False, **common)

    def test_mixed_project_chooses_input_mode_per_source_and_records_provenance(self):
        self.run_extraction()
        modes = {call["source_name"]: call["input_mode"] for call in self.calls}
        self.assertEqual(modes, {"a.pdf": "cloudflare_pdf", "b.xml": XML_TEXT_INPUT_MODE})
        records = {record["source_name"]: record for record in load_extractions(self.project)}
        pdf_hash = hashlib.sha256(b"%PDF-a").hexdigest()
        xml_hash = hashlib.sha256(JATS_XML.encode()).hexdigest()
        self.assertEqual(
            {name: (rec["source_format"], rec["source_sha256"], rec["source_pdf_sha256"]) for name, rec in records.items()},
            {"a.pdf": ("pdf", pdf_hash, pdf_hash), "b.xml": ("xml", xml_hash, None)},
        )
        log = {row["source_name"]: row for row in load_run_log(self.project)}
        self.assertEqual(log["b.xml"]["source_format"], "xml")
        self.assertIn("source_sha256", (self.project / "output/run_log.csv").read_text().splitlines()[0])

    def test_source_removed_mid_run_is_saved_without_a_hash(self):
        result = ExtractionResult(self.project / "sources/gone.xml", "gone.xml", "error")
        r._record_source(result, self.project / "sources/gone.xml", "xml")
        self.assertEqual((result.source_format, result.source_sha256), ("xml", None))

    def test_failed_xml_is_recorded_and_retried_as_text(self):
        with patch.object(r, "validate_model_option", return_value=self.option), \
                patch.object(r, "extract_document", side_effect=SourceError("Article is ~280k tokens; this model accepts 128k.")):
            r.run_project(project_dir=self.project, settings=self.settings, skip_report=True, console=Console(),
                          api_key="secret", assume_yes=True, interactive=False, force=False)
        failed = {rec["source_name"]: rec for rec in load_extractions(self.project)}["b.xml"]
        self.assertEqual((failed["status"], failed["input_mode"], failed["source_format"]), ("error", XML_TEXT_INPUT_MODE, "xml"))
        self.assertIn("this model accepts 128k", failed["error"])
        self.run_extraction(retry=True)
        self.assertEqual({call["source_name"]: call["input_mode"] for call in self.calls}["b.xml"], XML_TEXT_INPUT_MODE)
        self.assertEqual({rec["source_name"]: rec["status"] for rec in load_extractions(self.project)}["b.xml"], "ok")


class ProjectAndWebTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "config")})
        env.start()
        self.addCleanup(env.stop)
        self.project = create_project("Test", root=root / "projects")
        self.app = w.create_app(token="token", projects_root=root / "projects")
        self.base = "/token/projects/" + self.project.project_id
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.addCleanup(self.client.close)

    def test_upload_names_accept_pdf_and_xml_only(self):
        self.assertEqual(safe_upload_name("../Study.XML"), "Study.XML")
        with self.assertRaisesRegex(ProjectError, "not a PDF or XML file"):
            safe_upload_name("notes.txt")

    def test_xml_uploads_are_validated(self):
        saved = save_upload(self.project, "jats.xml", io.BytesIO(JATS_XML.encode()))
        self.assertEqual(saved.read_text(encoding="utf-8"), JATS_XML)
        for data, message in [(b"", "is empty"), (b"\x89PNG\r\n\x1a\n\x00\x00", "not UTF-8"), (b"Plain text", "does not look like XML")]:
            with self.subTest(data=data), self.assertRaisesRegex(ProjectError, message):
                save_upload(self.project, "bad.xml", io.BytesIO(data))
        self.assertEqual(sorted(p.name for p in (self.project.path / "sources").iterdir()), ["jats.xml"])
        response = self.client.post(self.base + "/uploads", files=[("files", ("bad.xml", b"hello", "text/xml"))])
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not look like XML", response.json()["detail"])

    def test_mixed_project_lists_serves_and_deletes_both_formats(self):
        response = self.client.post(self.base + "/uploads", files=[
            ("files", ("a.pdf", b"%PDF-a", "application/pdf")),
            ("files", ("b.xml", ELSEVIER_XML.encode(), "application/xml")),
        ])
        self.assertEqual(response.json(), {"saved": ["a.pdf", "b.xml"]})
        snapshot = project_snapshot(self.project)
        self.assertEqual([(a["name"], a["source_format"]) for a in snapshot["articles"]], [("a.pdf", "pdf"), ("b.xml", "xml")])
        self.assertEqual(snapshot["pdf_count"], 2)
        self.assertEqual(w._project_index_snapshot(self.project)["pdf_count"], 2)

        xml = self.client.get(self.base + "/sources/b.xml")
        self.assertEqual(xml.status_code, 200)
        self.assertEqual(xml.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(xml.headers["x-content-type-options"], "nosniff")
        self.assertEqual(xml.text, ELSEVIER_XML)
        self.assertEqual(self.client.get(self.base + "/sources/a.pdf").headers["content-type"], "application/pdf")
        self.assertEqual(self.client.get(self.base + "/sources/notes.txt").status_code, 404)

        save_extractions(self.project.path, [
            {"source_name": "a.pdf", "status": "ok", "effects": 1},
            {"source_name": "b.xml", "status": "error", "error": "Article is ~280k tokens; this model accepts 128k."},
        ])
        self.assertIn("this model accepts 128k", project_snapshot(self.project)["articles"][1]["error"])
        deleted = self.client.post(self.base + "/responses/delete", data={"source_name": "b.xml"})
        self.assertEqual(deleted.json(), {"deleted": True, "source_name": "b.xml"})
        self.assertEqual([rec["source_name"] for rec in load_extractions(self.project.path)], ["a.pdf"])

    def test_results_saved_before_source_formats_still_load(self):
        (self.project.path / "sources/old.pdf").write_bytes(b"%PDF-old")
        save_extractions(self.project.path, [{
            "source_name": "old.pdf", "status": "ok", "effects": 2, "input_mode": "native_pdf",
            "source_pdf_sha256": "abc", "data": {"effects": [{"claim": "x"}, {"claim": "y"}]},
        }])
        snapshot = project_snapshot(self.project)
        self.assertEqual((snapshot["articles"][0]["status"], snapshot["articles"][0]["effects"]), ("ok", 2))
        self.assertEqual(r.summarize(self.project.path, None).extracted_effects, 2)
        self.assertEqual(self.client.get(self.base).status_code, 200)


if __name__ == "__main__":
    unittest.main()
