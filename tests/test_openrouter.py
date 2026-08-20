from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_zcurve.models import (
    list_live_models,
    normalize_model_name,
    validate_model,
    validate_model_option,
)
from auto_zcurve.openrouter import MAX_COMPLETION_ATTEMPTS, extract_pdf, strict_response_schema
from auto_zcurve.schema import build_response_schema, parse_extraction_schema


MODEL_FIXTURES = [
    {
        "id": "vendor/compatible",
        "name": "Compatible",
        "supported_parameters": ["structured_outputs", "temperature"],
        "architecture": {"input_modalities": ["text", "file"], "output_modalities": ["text"]},
    },
    {
        "id": "qwen/qwen3.8-max",
        "name": "Qwen 3.8 Max",
        "context_length": 1_000_000,
        "reasoning": {"mandatory": True, "default_effort": "xhigh"},
        "supported_parameters": ["structured_outputs"],
        "architecture": {
            "input_modalities": ["text", "image", "video"],
            "output_modalities": ["text"],
        },
    },
    {
        "id": "deepseek/deepseek-v3.2",
        "name": "DeepSeek V3.2",
        "context_length": 163840,
        "supported_parameters": ["structured_outputs"],
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    },
    {
        "id": "vendor/no-schema",
        "supported_parameters": ["response_format"],
        "architecture": {"input_modalities": ["text", "file"], "output_modalities": ["text"]},
    },
    {
        "id": "vendor/compatible:batch",
        "name": "Compatible (batch)",
        "supported_parameters": ["structured_outputs"],
        "architecture": {"input_modalities": ["text", "file"], "output_modalities": ["text"]},
    },
]


class OpenRouterTests(unittest.TestCase):
    def setUp(self):
        import auto_zcurve.models as models

        models._OPENROUTER_CACHE = None
        self.model_config = patch(
            "auto_zcurve.models.MODEL_CONFIG",
            Path("/nonexistent-auto-zcurve-models.yml"),
        )
        self.model_config.start()

    def tearDown(self):
        self.model_config.stop()

    @patch("auto_zcurve.openrouter.list_models", return_value=MODEL_FIXTURES)
    def test_model_catalog_includes_native_pdf_and_structured_text_models(self, list_models):
        # OpenRouter models are manual-entry only (no browsable live catalog);
        # validate_model_option still checks the entered ID against it.
        self.assertEqual(list_live_models("key", "openrouter"), [])
        deepseek = validate_model_option(
            "openrouter", "deepseek/deepseek-v3.2", "key"
        )
        self.assertEqual(deepseek.input_mode, "cloudflare_pdf")
        self.assertEqual(deepseek.context_length, 163840)
        qwen = validate_model_option("openrouter", "qwen/qwen3.8-max", "key")
        self.assertEqual(qwen.input_mode, "cloudflare_pdf")
        self.assertEqual(qwen.context_length, 1_000_000)
        self.assertTrue(qwen.supports_reasoning)
        self.assertEqual(validate_model("openrouter", "vendor/compatible", "key"), "vendor/compatible")
        with self.assertRaisesRegex(ValueError, "batch-only models"):
            validate_model("openrouter", "vendor/compatible:batch", "key")
        self.assertGreaterEqual(list_models.call_count, 1)

    def test_batch_only_model_is_rejected_without_starting_a_request(self):
        with self.assertRaisesRegex(ValueError, "batch-only models"):
            normalize_model_name("google/gemini-3.7-flash:batch", "openrouter")

    def test_strict_schema_requires_nullable_optional_fields_and_forbids_extras(self):
        config = parse_extraction_schema(
            "name: result\n"
            "meta_data:\n  doi:\n    type: string\n"
            "effects:\n"
            "  claim:\n    type: string\n    required: true\n"
            "  notes:\n    type: string\n"
        )
        converted = strict_response_schema(build_response_schema(config))
        self.assertFalse(converted["additionalProperties"])
        effect = converted["properties"]["effects"]["items"]
        self.assertEqual(effect["required"], ["claim", "notes"])
        self.assertEqual(effect["properties"]["notes"]["type"], ["string", "null"])
        self.assertEqual(effect["properties"]["claim"]["type"], "string")
        self.assertFalse(effect["additionalProperties"])

    def test_extract_pdf_sends_native_file_and_strict_response_format(self):
        config = parse_extraction_schema(
            "name: result\neffects:\n  claim:\n    type: string\n    required: true\n"
        )
        response_schema = build_response_schema(config)
        response = {
            "model": "vendor/compatible:provider",
            "choices": [{"message": {"content": '{"effects":[{"claim":"Finding"}]}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
        }
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "study.pdf"
            pdf.write_bytes(b"%PDF-fixture")
            with (
                patch("auto_zcurve.openrouter.build_system_prompt", return_value="Extract"),
                patch("auto_zcurve.openrouter._request_json", return_value=response) as request,
            ):
                result = extract_pdf(
                    source_path=pdf,
                    source_name=pdf.name,
                    api_key="secret",
                    primary_model="vendor/compatible",
                    response_schema=response_schema,
                    schema_config=config,
                    instruction_path=Path(tmp) / "unused.md",
                    effect_definition=None,
                    reasoning_effort="high",
                )

        payload = request.call_args.kwargs["payload"]
        self.assertEqual(payload["provider"], {"require_parameters": True})
        self.assertEqual(payload["plugins"], [{"id": "file-parser", "pdf": {"engine": "native"}}])
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertTrue(payload["messages"][0]["content"][1]["file"]["file_data"].startswith("data:application/pdf;base64,"))
        self.assertNotIn("response-healing", str(payload))
        self.assertEqual(result.provider_used, "openrouter")
        self.assertEqual(result.model_used, "vendor/compatible:provider")
        self.assertEqual(result.total_tokens, 17)
        self.assertEqual(result.input_mode, "native_pdf")
        self.assertEqual(result.provider_responses, [{"attempt": 1, "response": response}])

    def test_extract_pdf_restricts_openrouter_endpoints(self):
        config = parse_extraction_schema(
            "name: result\neffects:\n  claim:\n    type: string\n    required: true\n"
        )
        response = {
            "model": "vendor/compatible",
            "choices": [{"message": {"content": '{"effects":[{"claim":"Finding"}]}'}}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "study.pdf"
            pdf.write_bytes(b"%PDF-fixture")
            with (
                patch("auto_zcurve.openrouter.build_system_prompt", return_value="Extract"),
                patch("auto_zcurve.openrouter._request_json", return_value=response) as request,
            ):
                extract_pdf(
                    source_path=pdf,
                    source_name=pdf.name,
                    api_key="secret",
                    primary_model="vendor/compatible",
                    response_schema=build_response_schema(config),
                    schema_config=config,
                    instruction_path=Path(tmp) / "unused.md",
                    effect_definition=None,
                    endpoint_order=("google-vertex/global", "deepinfra/fp8"),
                )

        self.assertEqual(
            request.call_args.kwargs["payload"]["provider"],
            {
                "require_parameters": True,
                "order": ["google-vertex/global", "deepinfra/fp8"],
                "only": ["google-vertex/global", "deepinfra/fp8"],
                "allow_fallbacks": False,
            },
        )

    def test_extract_pdf_uses_cloudflare_parser_for_deepseek_by_default_mode(self):
        config = parse_extraction_schema(
            "name: result\neffects:\n  claim:\n    type: string\n    required: true\n"
        )
        response = {
            "model": "deepseek/deepseek-v4-flash",
            "choices": [{"message": {"content": '{"effects":[{"claim":"Finding"}]}'}}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "study.pdf"
            pdf.write_bytes(b"%PDF-fixture")
            with (
                patch("auto_zcurve.openrouter.build_system_prompt", return_value="Extract"),
                patch("auto_zcurve.openrouter._request_json", return_value=response) as request,
            ):
                result = extract_pdf(
                    source_path=pdf,
                    source_name=pdf.name,
                    api_key="secret",
                    primary_model="deepseek/deepseek-v4-flash",
                    response_schema=build_response_schema(config),
                    schema_config=config,
                    instruction_path=root / "unused.md",
                    effect_definition=None,
                    input_mode="cloudflare_pdf",
                    context_length=1_048_576,
                    project_dir=root,
                )

        payload = request.call_args.kwargs["payload"]
        self.assertEqual(
            payload["plugins"],
            [{"id": "file-parser", "pdf": {"engine": "cloudflare-ai"}}],
        )
        self.assertEqual(payload["max_tokens"], 32768)
        self.assertIn("file_data", str(payload["messages"]))
        self.assertEqual(result.input_mode, "cloudflare_pdf")


if __name__ == "__main__":
    unittest.main()
