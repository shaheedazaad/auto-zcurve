import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from auto_zcurve import openrouter as p
from auto_zcurve.schema import parse_extraction_schema, build_response_schema


class OpenRouterTransportTests(unittest.TestCase):
    def test_get_post_headers_payload_and_timeout(self):
        for key, payload in [(None, None), ('secret', {'text': 'café'})]:
            with patch.object(p.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"data": []}')) as request:
                self.assertEqual(p._request_json('https://example.test', api_key=key, payload=payload, timeout_sec=9), {'data': []})
            sent = request.call_args.args[0]
            self.assertEqual(sent.get_method(), 'GET' if payload is None else 'POST')
            self.assertEqual(sent.get_header('Authorization'), None if key is None else 'Bearer secret')
            self.assertEqual(request.call_args.kwargs['timeout'], 9)
            if payload is not None:
                self.assertEqual(json.loads(sent.data), payload)
                self.assertEqual(sent.get_header('X-title'), 'auto-zcurve')
        with patch.object(p.urllib.request, 'urlopen', return_value=io.BytesIO(b'[]')), self.assertRaisesRegex(RuntimeError, 'invalid response payload'):
            p._request_json('https://example.test')

    def test_http_errors_preserve_payload_and_retry_after(self):
        cases = [({'error': {'message': 'nested'}}, 'nested'), ({'error': 'string error'}, 'string error'), ({'message': 'top'}, 'top'), (['details'], 'details')]
        for body, message in cases:
            for retry_after, expected in [('2.5', 2.5), ('bad', None)]:
                error = urllib.error.HTTPError('https://example.test', 429, 'Too many', {'Retry-After': retry_after}, io.BytesIO(json.dumps(body).encode()))
                with self.subTest(body=body, retry_after=retry_after), patch.object(p.urllib.request, 'urlopen', side_effect=error), self.assertRaisesRegex(p._OpenRouterRequestError, message) as caught:
                    p._request_json('https://example.test')
                self.assertEqual(caught.exception.status_code, 429)
                self.assertEqual(caught.exception.retry_after_sec, expected)
                self.assertEqual(caught.exception.raw_response, body if isinstance(body, dict) else {'body': body})
                self.assertTrue(caught.exception.retryable)
        error = urllib.error.HTTPError('https://example.test', 401, 'Unauthorized', {}, io.BytesIO(b'not json'))
        with patch.object(p.urllib.request, 'urlopen', side_effect=error), self.assertRaisesRegex(p._OpenRouterRequestError, 'Unauthorized') as caught:
            p._request_json('https://example.test')
        self.assertFalse(caught.exception.retryable)
        self.assertIsNone(caught.exception.raw_response)

    def test_network_failures_are_retryable(self):
        for error in [urllib.error.URLError('offline'), TimeoutError('timeout')]:
            with patch.object(p.urllib.request, 'urlopen', side_effect=error), self.assertRaises(p._OpenRouterRequestError) as caught:
                p._request_json('https://example.test')
            self.assertTrue(caught.exception.retryable)

    def test_catalog_shape_and_filters(self):
        for strict in [True, False]:
            with patch.object(p, '_request_json', return_value={'data': [{'id': 'a'}, None, 'bad']}) as request:
                self.assertEqual(p.list_models(structured_outputs_only=strict), [{'id': 'a'}])
                self.assertEqual('supported_parameters' in request.call_args.args[0], strict)
        with patch.object(p, '_request_json', return_value={'data': {}}), self.assertRaisesRegex(RuntimeError, 'invalid model catalog'):
            p.list_models()
        self.assertTrue(p.supports_native_structured_pdf({'id': 'a', 'supported_parameters': ['structured_outputs'], 'architecture': {'input_modalities': ['file']}}))
        self.assertIsNone(p.model_input_mode({'id': 'a', 'supported_parameters': ['structured_outputs'], 'architecture': {'input_modalities': ['image']}}))
        self.assertIsNone(p.model_input_mode({'id': 'a:batch', 'supported_parameters': ['structured_outputs'], 'architecture': {'input_modalities': ['text', 'file']}}))

    def test_message_usage_schema_and_retry_helpers(self):
        self.assertEqual(p._message_text(None), '')
        self.assertEqual(p._message_text({'content': None}), '')
        self.assertEqual(p._message_text({'content': [{'type': 'text', 'text': 'a'}, {'type': 'output_text', 'text': 'b'}, None, {'type': 'image'}]}), 'ab')
        self.assertEqual(p._usage({'usage': {'prompt_tokens': '4', 'completion_tokens': [], 'total_tokens': None}}), {'input_tokens': 4, 'output_tokens': None, 'total_tokens': None})
        self.assertFalse(p._retryable_status('bad'))
        self.assertEqual(p._retry_delay(2), 2)
        self.assertEqual(p._retry_delay(1, p._OpenRouterRequestError('wait', retry_after_sec=60)), 30)
        self.assertEqual(p._retry_delay(2, p._OpenRouterRequestError('wait', retry_after_sec=-1)), 2)
        schema = {'type': 'object', 'properties': {'x': {'type': ['string']}, 'y': {'type': ['string', 'null']}, 'z': {'description': 'untyped'}}}
        converted = p.strict_response_schema(schema)
        self.assertEqual(converted['properties']['x']['type'], ['string', 'null'])
        self.assertEqual(converted['properties']['y']['type'], ['string', 'null'])
        self.assertEqual(converted['required'], ['x', 'y', 'z'])
        self.assertEqual(p.strict_response_schema({'type': 'object', 'properties': None})['properties'], {})


class OpenRouterExtractionFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        source = root / 'study.pdf'
        source.write_bytes(b'%PDF-fixture')
        prompt = root / 'prompt.md'
        prompt.write_text('extract')
        schema = parse_extraction_schema('effects: {claim: {type: string, required: true}}')
        self.kwargs = dict(source_path=source, source_name='study.pdf', api_key='secret', primary_model='vendor/model', response_schema=build_response_schema(schema), schema_config=schema, instruction_path=prompt)
        self.sleep = patch.object(p.time, 'sleep').start()
        self.addCleanup(self.sleep.stop)

    def success(self):
        return {'model': 'actual/model', 'choices': [{'message': {'content': '{"effects":[{"claim":"finding"}]}'}}], 'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7}}

    def test_unsupported_input_mode(self):
        with self.assertRaisesRegex(RuntimeError, 'Unsupported OpenRouter input mode'):
            p.extract_pdf(**self.kwargs, input_mode='local_text')

    def test_retryable_transport_failure_then_success(self):
        error = p._OpenRouterRequestError('busy', status_code=503, retry_after_sec=0.5, raw_response={'error': 'busy'})
        with patch.object(p, '_request_json', side_effect=[error, self.success()]):
            result = p.extract_pdf(**self.kwargs)
        self.sleep.assert_called_once_with(0.5)
        self.assertEqual(result.model_used, 'actual/model')
        self.assertEqual(result.total_tokens, 7)
        self.assertEqual(result.provider_responses[0], {'attempt': 1, 'response': {'error': 'busy'}})

    def test_nonretryable_native_error_and_cloudflare_fallback(self):
        for mode, attempts in [('native_pdf', 1), ('cloudflare_pdf', 2)]:
            errors = [p._OpenRouterRequestError('bad', status_code=400) for _ in range(3)]
            engines = []
            def request(*args, **kwargs):
                engines.append(kwargs['payload']['plugins'][0]['pdf']['engine'])
                raise errors[len(engines) - 1]
            with self.subTest(mode=mode), patch.object(p, '_request_json', side_effect=request), self.assertRaisesRegex(p._OpenRouterRequestError, 'bad') as caught:
                p.extract_pdf(**self.kwargs, input_mode=mode)
            self.assertEqual(len(engines), attempts)
            self.assertEqual(caught.exception.provider_responses, [])
            if mode == 'cloudflare_pdf':
                self.assertEqual(engines, ['cloudflare-ai', 'mistral-ocr'])

    def test_response_failures_retry_and_preserve_all_attempts(self):
        cases = [({'error': {'code': 503, 'message': 'busy'}}, 'generation failed'), ({'choices': [{'finish_reason': 'length'}], 'usage': {'completion_tokens': 10, 'completion_tokens_details': {'reasoning_tokens': 9}}}, 'reasoning tokens: 9'), ({'choices': [{'message': {'content': 'bad json'}}]}, 'invalid JSON'), ({'choices': [{'message': {'content': '{"effects":[{}]}'}}]}, 'Missing required field')]
        for response, message in cases:
            with self.subTest(message=message), patch.object(p, '_request_json', return_value=response) as request, self.assertRaisesRegex((ValueError, RuntimeError), message) as caught:
                p.extract_pdf(**self.kwargs)
            self.assertEqual(request.call_count, 3)
            self.assertEqual(len(caught.exception.provider_responses), 3)
            with patch.object(p, '_request_json', side_effect=[response, self.success()]):
                result = p.extract_pdf(**self.kwargs)
            self.assertEqual(result.status, 'ok')
            self.assertEqual(len(result.provider_responses), 2)

    def test_embedded_error_categories(self):
        for payload, retryable in [({'error': {'code': 401}}, False), ({'choices': [{'error': {'metadata': {'error_type': 'timeout'}}}]}, True), ({'choices': [{'error': {'metadata': {'error_type': 'invalid_request'}}}]}, False), ({'choices': [{'finish_reason': 'error'}]}, True)]:
            detail, actual = p._embedded_error(payload)
            self.assertEqual(actual, retryable)
            self.assertIn('generation failed', detail)
        with patch.object(p, '_request_json', return_value={'error': {'code': 401}}) as request, self.assertRaisesRegex(RuntimeError, 'generation failed'):
            p.extract_pdf(**self.kwargs)
        request.assert_called_once()
        self.assertIn('unknown', p._empty_response_error({}))
