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
from auto_zcurve.openrouter import (
    MAX_COMPLETION_ATTEMPTS,
    endpoint_output_limit,
    extract_document,
    list_model_endpoints,
    strict_response_schema,
)
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

    @patch("auto_zcurve.openrouter.list_models")
    def test_model_catalog_includes_native_pdf_and_structured_text_models(self, list_models):
        # Like OpenRouter, the catalog leaves out models without structured outputs unless asked.
        def catalog(*, structured_outputs_only=True, **kwargs):
            return [item for item in MODEL_FIXTURES
                    if not structured_outputs_only or "structured_outputs" in item["supported_parameters"]]

        list_models.side_effect = catalog
        # The catalog is offered as suggestions; validate_model_option checks the entered ID.
        suggested = [option.name for option in list_live_models("key", "openrouter")]
        self.assertEqual(suggested, ["deepseek/deepseek-v3.2", "qwen/qwen3.8-max", "vendor/compatible"])
        self.assertNotIn("structured_outputs_only", list_models.call_args.kwargs)
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
                result = extract_document(
                    source_path=pdf,
                    source_name=pdf.name,
                    api_key="secret",
                    primary_model="vendor/compatible",
                    response_schema=response_schema,
                    schema_config=config,
                    instruction_path=Path(tmp) / "unused.md",
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
                extract_document(
                    source_path=pdf,
                    source_name=pdf.name,
                    api_key="secret",
                    primary_model="vendor/compatible",
                    response_schema=build_response_schema(config),
                    schema_config=config,
                    instruction_path=Path(tmp) / "unused.md",
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

    def test_extract_pdf_applies_preferred_provider_and_fallback_choice(self):
        config = parse_extraction_schema(
            "name: result\neffects:\n  claim:\n    type: string\n    required: true\n"
        )
        response = {
            "model": "vendor/compatible",
            "choices": [{"message": {"content": '{"effects":[{"claim":"Finding"}]}'}}],
        }
        cases = [
            ("", True, {"require_parameters": True}),
            ("", False, {"require_parameters": True, "allow_fallbacks": False}),
            ("deepinfra/fp8", True, {"require_parameters": True, "order": ["deepinfra/fp8"], "allow_fallbacks": True}),
            ("deepinfra/fp8", False, {"require_parameters": True, "order": ["deepinfra/fp8"], "allow_fallbacks": False}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "study.pdf"
            pdf.write_bytes(b"%PDF-fixture")
            for preferred, allow, expected in cases:
                with (
                    self.subTest(preferred=preferred, allow=allow),
                    patch("auto_zcurve.openrouter.build_system_prompt", return_value="Extract"),
                    patch("auto_zcurve.openrouter._request_json", return_value=response) as request,
                ):
                    extract_document(
                        source_path=pdf,
                        source_name=pdf.name,
                        api_key="secret",
                        primary_model="vendor/compatible",
                        response_schema=build_response_schema(config),
                        schema_config=config,
                        instruction_path=Path(tmp) / "unused.md",
                        preferred_provider=preferred,
                        allow_fallbacks=allow,
                    )
                    self.assertEqual(request.call_args.kwargs["payload"]["provider"], expected)

    def test_list_model_endpoints_keeps_structured_output_endpoints(self):
        payload = {"data": {"endpoints": [
            {"tag": "deepinfra/turbo", "provider_name": "DeepInfra", "quantization": "fp8", "supported_parameters": ["structured_outputs"]},
            {"tag": "groq", "provider_name": "Groq", "quantization": "unknown", "supported_parameters": ["tools"]},
            {"tag": "google-vertex", "provider_name": "Google Vertex", "quantization": "unknown", "supported_parameters": ["structured_outputs"]},
            {"tag": "deepinfra/turbo", "provider_name": "DeepInfra", "supported_parameters": ["structured_outputs"]},
            {"tag": "akashml/fp8", "provider_name": "AkashML", "quantization": "fp8", "supported_parameters": ["structured_outputs"]},
            {"provider_name": "No tag", "supported_parameters": ["structured_outputs"]},
            "not an endpoint",
        ]}}
        with patch("auto_zcurve.openrouter._request_json", return_value=payload) as request:
            endpoints = list_model_endpoints("meta-llama/llama 3:free", api_key="secret")
        self.assertEqual(request.call_args.args[0], "https://openrouter.ai/api/v1/models/meta-llama/llama%203/endpoints")
        self.assertEqual(endpoints, [
            {"tag": "akashml/fp8", "name": "AkashML (fp8)"},
            {"tag": "deepinfra/turbo", "name": "DeepInfra (turbo, fp8)"},
            {"tag": "google-vertex", "name": "Google Vertex"},
        ])
        for bad in [{}, {"data": []}, {"data": {"endpoints": {}}}]:
            with patch("auto_zcurve.openrouter._request_json", return_value=bad), self.assertRaisesRegex(RuntimeError, "invalid endpoint list"):
                list_model_endpoints("vendor/model")

    def test_endpoint_output_limit_is_the_lowest_of_the_likely_endpoints(self):
        def endpoint(tag, limit, context=1_000_000, structured=True):
            return {
                "tag": tag,
                "max_completion_tokens": limit,
                "context_length": context,
                "supported_parameters": ["structured_outputs"] if structured else [],
            }

        def limit(endpoints, **kwargs):
            with patch("auto_zcurve.openrouter._request_json", return_value={"data": {"endpoints": endpoints}}):
                return endpoint_output_limit("vendor/model", **kwargs)

        endpoints = [
            endpoint("deepinfra/fp8", 393_216),
            endpoint("deepinfra/fp8/flex", 400_000),
            endpoint("wafer/us", 600_000),
            endpoint("particle-ai/us", 131_072),
            endpoint("tiny", 1_000, structured=False),
            endpoint("unknown", None),
            endpoint("odd", "lots"),
        ]
        # Automatic routing: any structured-output endpoint may serve the request.
        self.assertEqual(limit(endpoints), 131_072)
        # A pinned provider is tried first, so only its endpoints count, fallbacks or not.
        self.assertEqual(limit(endpoints, preferred_provider="deepinfra/fp8"), 393_216)
        self.assertEqual(limit(endpoints, preferred_provider="wafer"), 600_000)
        # A pin that matches no endpoint, or none stating a limit, falls back to every endpoint.
        self.assertEqual(limit(endpoints, preferred_provider="deep"), 131_072)
        self.assertEqual(limit(endpoints, preferred_provider="unknown"), 131_072)
        self.assertIsNone(limit([endpoint("unknown", None), endpoint("tiny", 1_000, structured=False)]))

        # Each endpoint leaves a quarter of its context, at least 32,768 tokens, for the article.
        self.assertEqual(limit([endpoint("same", 200_000, context=200_000)]), 150_000)
        self.assertEqual(limit([endpoint("small", 65_536, context=65_536)]), 32_768)
        self.assertEqual(limit([endpoint("roomy", 8_192, context=200_000)]), 8_192)
        self.assertEqual(limit([endpoint("no-context", 50_000, context=None)]), 50_000)
        self.assertEqual(limit([endpoint("odd-context", 50_000, context="lots")]), 50_000)
        self.assertIsNone(limit([endpoint("cramped", 30_000, context=30_000)]))
        self.assertEqual(
            limit([endpoint("cramped", 30_000, context=30_000), endpoint("roomy", 100_000, context=1_000_000)]),
            100_000,
        )

    def test_extract_sends_the_output_limit_only_when_one_is_given(self):
        config = parse_extraction_schema(
            "name: result\neffects:\n  claim:\n    type: string\n    required: true\n"
        )
        response = {"choices": [{"message": {"content": '{"effects":[{"claim":"Finding"}]}'}}]}
        for limit in (None, 393_216):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pdf = root / "study.pdf"
                pdf.write_bytes(b"%PDF-fixture")
                with (
                    patch("auto_zcurve.openrouter.build_system_prompt", return_value="Extract"),
                    patch("auto_zcurve.openrouter._request_json", return_value=response) as request,
                ):
                    extract_document(
                        source_path=pdf,
                        source_name=pdf.name,
                        api_key="secret",
                        primary_model="vendor/model",
                        response_schema=build_response_schema(config),
                        schema_config=config,
                        instruction_path=root / "unused.md",
                        input_mode="cloudflare_pdf",
                        max_output_tokens=limit,
                    )
                payload = request.call_args.kwargs["payload"]
                self.assertEqual(payload.get("max_tokens"), limit)

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
                result = extract_document(
                    source_path=pdf,
                    source_name=pdf.name,
                    api_key="secret",
                    primary_model="deepseek/deepseek-v4-flash",
                    response_schema=build_response_schema(config),
                    schema_config=config,
                    instruction_path=root / "unused.md",
                    input_mode="cloudflare_pdf",
                    context_length=1_048_576,
                    project_dir=root,
                )

        payload = request.call_args.kwargs["payload"]
        self.assertEqual(
            payload["plugins"],
            [{"id": "file-parser", "pdf": {"engine": "cloudflare-ai"}}],
        )
        self.assertNotIn("max_tokens", payload)
        self.assertIn("file_data", str(payload["messages"]))
        self.assertEqual(result.input_mode, "cloudflare_pdf")


if __name__ == "__main__":
    unittest.main()
