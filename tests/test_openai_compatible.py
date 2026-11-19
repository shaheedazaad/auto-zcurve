from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from auto_zcurve.openai_compatible import ProviderError, _call, extract_document, normalize_base_url, pdf_text
from auto_zcurve.schema import build_response_schema, parse_extraction_schema
from auto_zcurve.sources import SourceError


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_url_validation(self):
        self.assertEqual(normalize_base_url(" http://localhost:8000/v1/ "), "http://localhost:8000/v1")
        for url in ["", "file:///tmp/x", "https://user:secret@host/v1", "https://host/v1?q=1",
                    "https://host/v1#fragment", "https://host/v1/chat/completions", "http://host:bad/v1"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_base_url(url)

    def test_output_modes_and_optional_auth(self):
        for mode in ("json_schema", "json_object", "none"):
            for key in ("", "secret"):
                response = {"choices": [{"message": {"content": '{"effects":[]}'}}]}
                with patch("auto_zcurve.openai_compatible.urllib.request.urlopen",
                           return_value=io.BytesIO(json.dumps(response).encode())) as request:
                    text, _, _ = _call(model="local-model", api_key=key, prompt="Extract",
                                    base_url="http://localhost:8000/v1", response_format=mode,
                                    response_schema={"type": "object"})
                sent = request.call_args.args[0]
                payload = json.loads(sent.data)
                self.assertEqual(sent.full_url, "http://localhost:8000/v1/chat/completions")
                self.assertEqual(sent.get_header("Authorization"), f"Bearer {key}" if key else None)
                self.assertEqual(payload["model"], "local-model")
                self.assertNotIn("plugins", payload)
                if mode == "none":
                    self.assertNotIn("response_format", payload)
                else:
                    self.assertEqual(payload["response_format"]["type"], mode)
                self.assertEqual(json.loads(text), {"effects": []})

    def test_extraction_validates_even_prompt_only_responses(self):
        schema = parse_extraction_schema("name: result\neffects:\n  claim:\n    type: string\n    required: true\n")
        kwargs = dict(source_path=self.root / "study.pdf", source_name="study.pdf", api_key="",
                      primary_model="local", response_schema=build_response_schema(schema),
                      schema_config=schema, instruction_path=self.root / "instructions.md",
                      base_url="http://localhost:8000/v1", response_format="none")
        with patch("auto_zcurve.openai_compatible.pdf_text", return_value="[Page 1]\nStudy"), patch(
            "auto_zcurve.prompts.build_system_prompt", return_value="Extract"
        ), patch("auto_zcurve.openai_compatible._call", return_value=(
            '{"effects":[{"claim":"Finding"}]}', {"input_tokens": 10, "output_tokens": 5}, "stop"
        )):
            result = extract_document(**kwargs)
        self.assertEqual(result.provider_used, "openai_compatible")
        self.assertEqual(result.total_tokens, 15)
        with patch("auto_zcurve.openai_compatible.pdf_text", return_value="Study"), patch(
            "auto_zcurve.prompts.build_system_prompt", return_value="Extract"
        ), patch("auto_zcurve.openai_compatible._call", return_value=('{"effects":[{}]}', {}, None)):
            with self.assertRaises(ValueError):
                extract_document(**kwargs)

    def test_pdf_text_too_long_for_the_context_limit_is_never_sent(self):
        schema = parse_extraction_schema("name: result\neffects:\n  claim:\n    type: string\n")
        with patch("auto_zcurve.openai_compatible.pdf_text", return_value="word " * 1000), patch(
            "auto_zcurve.openai_compatible._call"
        ) as call, self.assertRaisesRegex(SourceError, "this model accepts 100"):
            extract_document(source_path=self.root / "study.pdf", source_name="study.pdf", api_key="",
                             primary_model="local", response_schema=build_response_schema(schema),
                             schema_config=schema, instruction_path=self.root / "instructions.md",
                             base_url="http://localhost:8000/v1", context_length=100)
        call.assert_not_called()

    def test_blank_pdf_has_actionable_error(self):
        path = self.root / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(path)
        with self.assertRaisesRegex(ProviderError, "OCR"):
            pdf_text(path, None)

