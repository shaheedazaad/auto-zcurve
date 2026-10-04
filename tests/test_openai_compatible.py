from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from auto_zcurve.config import AppSettings, load_app_settings, save_app_settings
from auto_zcurve.openai_compatible import ProviderError, _call, extract_pdf, normalize_base_url, pdf_text
from auto_zcurve.projects import create_project
from auto_zcurve.schema import build_response_schema, parse_extraction_schema
from auto_zcurve.web import create_app


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
                    text, _ = _call(model="local-model", api_key=key, prompt="Extract",
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
        save_app_settings(AppSettings(openai_base_url="http://localhost:8000/v1", openai_response_format="none"))
        schema = parse_extraction_schema("name: result\neffects:\n  claim:\n    type: string\n    required: true\n")
        kwargs = dict(source_path=self.root / "study.pdf", source_name="study.pdf", api_key="",
                      primary_model="local", response_schema=build_response_schema(schema),
                      schema_config=schema, instruction_path=self.root / "instructions.md")
        with patch("auto_zcurve.openai_compatible.pdf_text", return_value="[Page 1]\nStudy"), patch(
            "auto_zcurve.prompts.build_system_prompt", return_value="Extract"
        ), patch("auto_zcurve.openai_compatible._call", return_value=(
            '{"effects":[{"claim":"Finding"}]}', {"input_tokens": 10, "output_tokens": 5}
        )):
            result = extract_pdf(**kwargs)
        self.assertEqual(result.provider_used, "openai_compatible")
        self.assertEqual(result.total_tokens, 15)
        with patch("auto_zcurve.openai_compatible.pdf_text", return_value="Study"), patch(
            "auto_zcurve.prompts.build_system_prompt", return_value="Extract"
        ), patch("auto_zcurve.openai_compatible._call", return_value=('{"effects":[{}]}', {})):
            with self.assertRaises(ValueError):
                extract_pdf(**kwargs)

    def test_blank_pdf_has_actionable_error(self):
        path = self.root / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(path)
        with self.assertRaisesRegex(ProviderError, "OCR"):
            pdf_text(path, None)

    def test_provider_defaults_are_saved_independently(self):
        save_app_settings(AppSettings(parallel_requests=7, openai_base_url="http://localhost:8000/v1"))
        app = create_app(token="test", projects_root=self.root / "projects")
        with TestClient(app, base_url="http://127.0.0.1") as client:
            for provider, field, value in [
                ("gemini", "default_gemini_model", "gemini-test"),
                ("openrouter", "default_openrouter_model", "vendor/test"),
            ]:
                response = client.post(f"/test/settings/providers/{provider}", data={field: value},
                                       headers={"Origin": "http://127.0.0.1"})
                self.assertEqual(response.status_code, 200)
            client.post("/test/settings", data={"parallel_requests": 4},
                        headers={"Origin": "http://127.0.0.1"})
        settings = load_app_settings()
        self.assertEqual(settings.default_gemini_model, "gemini-test")
        self.assertEqual(settings.default_openrouter_model, "vendor/test")
        self.assertEqual(settings.openai_base_url, "http://localhost:8000/v1")
        self.assertEqual(settings.parallel_requests, 4)

    def test_endpoint_settings_and_keyless_run_and_retry(self):
        app = create_app(token="test", projects_root=self.root / "projects")
        project = create_project("Endpoint", root=self.root / "projects")
        with TestClient(app, base_url="http://127.0.0.1") as client:
            response = client.post('/test/settings/endpoint', data={"openai_base_url": "http://localhost:8000/v1/",
                                                         "openai_response_format": "json_object"},
                                   headers={"Origin": "http://127.0.0.1"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(load_app_settings().openai_base_url, "http://localhost:8000/v1")
            self.assertEqual(load_app_settings().openai_response_format, "json_object")
            client.post('/test/settings', data={"parallel_requests": 4},
                        headers={"Origin": "http://127.0.0.1"})
            self.assertEqual(load_app_settings().openai_base_url, "http://localhost:8000/v1")
            self.assertEqual(load_app_settings().parallel_requests, 4)
            page = client.get(f'/test/projects/{project.project_id}')
            self.assertIn('data-endpoint-ready="true"', page.text)
            with patch.object(app.state.runtime, "start_job", return_value=SimpleNamespace(status="queued")) as start:
                for action in ("run", "retry"):
                    response = client.post(f'/test/projects/{project.project_id}/{action}',
                                           data={"provider": "openai_compatible", "model": "local-model"},
                                           headers={"Origin": "http://127.0.0.1"})
                    self.assertEqual(response.status_code, 202, response.text)
                    self.assertEqual(start.call_args.args[2].provider, "openai_compatible")
            bad = client.post('/test/settings', data={"openai_base_url": "https://user:secret@host/v1"},
                              headers={"Origin": "http://127.0.0.1"})
            self.assertEqual(bad.status_code, 400)
            self.assertEqual(load_app_settings().openai_base_url, "http://localhost:8000/v1")
