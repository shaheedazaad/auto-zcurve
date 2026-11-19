"""needs_review for the OpenRouter and OpenAI-compatible providers (issue #2)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_zcurve import openai_compatible as oc
from auto_zcurve import openrouter as orr
from auto_zcurve.artifacts import load_extractions, upsert_extraction
from auto_zcurve.config import AppSettings, save_app_settings
from auto_zcurve.llm import parse_json_with_repair
from auto_zcurve.schema import build_response_schema, parse_extraction_schema

VALID = '{"effects":[{"claim":"finding"}]}'
TRUNCATED = '{"effects":[{"claim":"finding"}'


def openrouter_response(content: str = VALID, **choice: object) -> dict:
    return {"model": "actual/model", "choices": [{"message": {"content": content}, **choice}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        source = self.root / "study.pdf"
        source.write_bytes(b"%PDF-fixture")
        prompt = self.root / "prompt.md"
        prompt.write_text("extract")
        schema = parse_extraction_schema("effects: {claim: {type: string, required: true}}")
        self.kwargs = dict(source_path=source, source_name="study.pdf", api_key="secret",
                           primary_model="vendor/model", response_schema=build_response_schema(schema),
                           schema_config=schema, instruction_path=prompt)


class OpenRouterFinishReasonTests(Fixture):
    def setUp(self):
        super().setUp()
        patcher = patch.object(orr.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def extract(self, *responses: dict):
        with patch.object(orr, "_request_json", side_effect=list(responses)):
            return orr.extract_document(**self.kwargs)

    def test_length_is_needs_review_with_values_and_reason(self):
        result = self.extract(openrouter_response(finish_reason="length"))
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.review_reasons, ("finish reason: length",))
        self.assertEqual(result.data, {"effects": [{"claim": "finding"}]})

    def test_other_abnormal_reasons_are_flagged(self):
        result = self.extract(openrouter_response(finish_reason="content_filter"))
        self.assertEqual((result.status, result.review_reasons), ("needs_review", ("finish reason: content_filter",)))

    def test_stop_and_missing_finish_reason_are_ok(self):
        for choice in ({"finish_reason": "stop"}, {"finish_reason": None}, {}):
            with self.subTest(choice=choice):
                result = self.extract(openrouter_response(**choice))
                self.assertEqual((result.status, result.review_reasons), ("ok", ()))

    def test_only_final_successful_attempt_is_checked(self):
        # An unrepairable first attempt is retried; the clean retry is ok.
        result = self.extract(openrouter_response("bad json", finish_reason="length"), openrouter_response(finish_reason="stop"))
        self.assertEqual((result.status, result.review_reasons), ("ok", ()))
        self.assertEqual(len(result.provider_responses), 2)
        # A truncated response that can be repaired is kept and flagged, not retried.
        result = self.extract(openrouter_response(TRUNCATED, finish_reason="length"))
        self.assertEqual(result.review_reasons, ("JSON was repaired", "finish reason: length"))
        self.assertEqual(result.repaired_response, '{"effects": [{"claim": "finding"}]}')
        # A retry after an empty response that then finishes with `length` is flagged.
        result = self.extract({"choices": [{"finish_reason": "stop"}]}, openrouter_response(finish_reason="length"))
        self.assertEqual(result.status, "needs_review")


class OpenAICompatibleCallTests(Fixture):
    def call(self, body: dict):
        with patch("auto_zcurve.openai_compatible.urllib.request.urlopen",
                   return_value=io.BytesIO(json.dumps(body).encode())):
            return oc._call(model="m", api_key="", prompt="Extract", base_url="http://localhost:8000/v1",
                            response_schema={"type": "object"})

    def test_call_returns_finish_reason(self):
        text, tokens, finish_reason = self.call({"choices": [{"message": {"content": VALID}, "finish_reason": "length"}]})
        self.assertEqual((text, finish_reason), (VALID, "length"))
        self.assertEqual(self.call({"choices": [{"message": {"content": VALID}}]})[2], None)
        self.assertEqual(self.call({"choices": [{"message": {"content": VALID}, "finish_reason": 3}]})[2], None)


class OpenAICompatibleExtractionTests(Fixture):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")})
        env.start()
        self.addCleanup(env.stop)
        save_app_settings(AppSettings(openai_base_url="http://localhost:8000/v1"))

    def extract(self, text: str, finish_reason: str | None):
        with patch.object(oc, "pdf_text", return_value="[Page 1]\nStudy"), patch.object(
            oc, "_call", return_value=(text, {"input_tokens": 3, "output_tokens": 2}, finish_reason)
        ):
            return oc.extract_document(**self.kwargs)

    def test_length_is_needs_review(self):
        result = self.extract(VALID, "length")
        self.assertEqual((result.status, result.review_reasons), ("needs_review", ("finish reason: length",)))
        self.assertEqual(result.data, {"effects": [{"claim": "finding"}]})

    def test_stop_and_missing_finish_reason_are_ok(self):
        for finish_reason in ("stop", None):
            with self.subTest(finish_reason=finish_reason):
                result = self.extract(VALID, finish_reason)
                self.assertEqual((result.status, result.review_reasons), ("ok", ()))
                self.assertIsNone(result.repaired_response)
                self.assertEqual(result.raw_response, VALID)

    def test_repaired_json_is_needs_review_and_both_responses_are_kept(self):
        result = self.extract(TRUNCATED, "length")
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.review_reasons, ("JSON was repaired", "finish reason: length"))
        self.assertEqual(result.raw_response, TRUNCATED)
        self.assertEqual(json.loads(result.repaired_response), {"effects": [{"claim": "finding"}]})
        project = self.root / "project"
        project.mkdir()
        upsert_extraction(project, result)
        record = load_extractions(project)[0]
        self.assertEqual((record["status"], record["json_repaired"]), ("needs_review", True))
        self.assertEqual((project / record["raw_response_path"]).read_text(), TRUNCATED)
        self.assertTrue((project / record["repaired_response_path"]).is_file())

    def test_unrepairable_json_still_fails(self):
        with patch("json_repair.repair_json", return_value="not json"), self.assertRaisesRegex(
            RuntimeError, "OpenAI-compatible endpoint returned invalid JSON"
        ) as caught:
            self.extract("{broken", "stop")
        self.assertEqual(caught.exception.raw_response, "{broken")
        self.assertEqual(caught.exception.repaired_response, "not json")


class SharedRepairHelperTests(unittest.TestCase):
    def test_provider_name_appears_in_errors(self):
        self.assertEqual(parse_json_with_repair('{"a":1}', "Example"), ({"a": 1}, None))
        with patch("json_repair.repair_json", side_effect=TypeError("x")), self.assertRaisesRegex(RuntimeError, "^Example returned invalid JSON"):
            parse_json_with_repair("{", "Example")
