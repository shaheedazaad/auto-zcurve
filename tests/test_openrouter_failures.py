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
        self.assertFalse(p.retryable_status('bad'))
        self.assertEqual(p.retry_delay(2), 2)
        self.assertEqual(p.retry_delay(1, p._OpenRouterRequestError('wait', retry_after_sec=60)), 30)
        self.assertEqual(p.retry_delay(2, p._OpenRouterRequestError('wait', retry_after_sec=-1)), 2)
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
            p.extract_document(**self.kwargs, input_mode='local_text')

    def test_retryable_transport_failure_then_success(self):
        error = p._OpenRouterRequestError('busy', status_code=503, retry_after_sec=0.5, raw_response={'error': 'busy'})
        with patch.object(p, '_request_json', side_effect=[error, self.success()]):
            result = p.extract_document(**self.kwargs)
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
                p.extract_document(**self.kwargs, input_mode=mode)
            self.assertEqual(len(engines), attempts)
            self.assertEqual(caught.exception.provider_responses, [])
            if mode == 'cloudflare_pdf':
                self.assertEqual(engines, ['cloudflare-ai', 'mistral-ocr'])

    def test_malformed_success_body_is_retried_with_the_fallback_parser(self):
        for body in (b'[]', b'not json'):
            engines = []
            def urlopen(request, timeout):
                engines.append(json.loads(request.data)['plugins'][0]['pdf']['engine'])
                return io.BytesIO(body if len(engines) == 1 else json.dumps(self.success()).encode())
            with self.subTest(body=body), patch.object(p.urllib.request, 'urlopen', side_effect=urlopen):
                result = p.extract_document(**self.kwargs, input_mode='cloudflare_pdf')
            self.assertEqual(engines, ['cloudflare-ai', 'mistral-ocr'])
            self.assertEqual(result.model_used, 'actual/model')

    def parsed(self, response, pages):
        """Attach OpenRouter's file-parser annotation, as the parser returned it, to a response."""
        text = '# document.pdf\n## Metadata\n- Title=External Behavior Monitoring\n\n\n## Contents\n' + ''.join(
            f'### Page {number}\n\n{body}\n\n' for number, body in enumerate(pages, 1)
        )
        content = [{'type': 'text', 'text': '<file name="study.pdf">'}, {'type': 'text', 'text': text}, {'type': 'text', 'text': '</file>'}]
        response['choices'][0]['message']['annotations'] = [{'type': 'file', 'file': {'name': 'study.pdf', 'content': content}}]
        return response

    def test_blank_cloudflare_parse_is_retried_with_mistral_ocr(self):
        engines = []
        blank = self.parsed(self.success(), ['', '', ''])
        blank['choices'][0]['message']['content'] = '{"effects":[]}'
        def request(*args, **kwargs):
            engines.append(kwargs['payload']['plugins'][0]['pdf']['engine'])
            return blank if len(engines) == 1 else self.parsed(self.success(), ['Abstract. ' * 40])
        with patch.object(p, '_request_json', side_effect=request):
            result = p.extract_document(**self.kwargs, input_mode='cloudflare_pdf')
        self.assertEqual(engines, ['cloudflare-ai', 'mistral-ocr'])
        self.assertEqual(result.data, {'effects': [{'claim': 'finding'}]})
        self.assertEqual(len(result.provider_responses), 2)
        self.assertEqual(result.total_tokens, 14)

    def test_pdf_that_no_parser_can_read_fails_instead_of_returning_no_effects(self):
        blank = self.parsed(self.success(), ['', ''])
        with patch.object(p, '_request_json', return_value=blank) as request, self.assertRaisesRegex(RuntimeError, 'no text') as caught:
            p.extract_document(**self.kwargs, input_mode='cloudflare_pdf')
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(caught.exception.provider_responses), 2)

    def test_parsed_text_check_needs_a_parser_annotation(self):
        self.assertIsNone(p._parsed_pdf_text(self.success()))
        other_annotations = self.success()
        other_annotations['choices'][0]['message']['annotations'] = [{'type': 'url_citation'}, {'type': 'file', 'file': {}}]
        self.assertIsNone(p._parsed_pdf_text(other_annotations))
        self.assertEqual(p._parsed_pdf_text(self.parsed(self.success(), ['', ''])), '')
        self.assertEqual(p._parsed_pdf_text(self.parsed(self.success(), ['Real text'])), 'Real text')
        with patch.object(p, '_request_json', return_value=self.parsed(self.success(), ['Short but real.'])):
            self.assertEqual(p.extract_document(**self.kwargs, input_mode='cloudflare_pdf').data, {'effects': [{'claim': 'finding'}]})

    def test_mostly_blank_parse_is_retried_with_the_fallback_parser(self):
        text = 'Results showed F(1, 27) = 29.31, p < .001.'
        blank = self.parsed(self.success(), [text] + [''] * 11)
        full = self.parsed(self.success(), [text] * 12)
        engines = []
        def request(*args, **kwargs):
            engines.append(kwargs['payload']['plugins'][0]['pdf']['engine'])
            return blank if len(engines) == 1 else full
        with patch.object(p, '_request_json', side_effect=request):
            result = p.extract_document(**self.kwargs, input_mode='cloudflare_pdf')
        self.assertEqual(engines, ['cloudflare-ai', 'mistral-ocr'])
        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.review_reasons, ())
        self.assertEqual([item['attempt'] for item in result.provider_responses], [1, 2])
        self.assertEqual(result.total_tokens, 14)
        self.sleep.assert_not_called()

    def test_blank_parse_from_both_parsers_is_flagged_for_review(self):
        blank = self.parsed(self.success(), ['Abstract text that is long enough to count.'] + [''] * 11)
        with patch.object(p, '_request_json', return_value=blank) as request:
            result = p.extract_document(**self.kwargs, input_mode='cloudflare_pdf')
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result.status, 'needs_review')
        self.assertEqual(result.review_reasons, ('PDF parser returned no text for 11 of 12 pages',))

    def test_good_or_short_parses_are_not_retried(self):
        text = 'Results showed F(1, 27) = 29.31, p < .001.'
        for pages in ([text] * 12, [text, text, ''], ['Short.']):
            with self.subTest(pages=len(pages)), patch.object(p, '_request_json', return_value=self.parsed(self.success(), pages)) as request:
                result = p.extract_document(**self.kwargs, input_mode='cloudflare_pdf')
            self.assertEqual(request.call_count, 1)
            self.assertEqual(result.review_reasons, ())

    def test_page_coverage_ignores_responses_without_a_parsed_document(self):
        for response in [{}, self.success(), {'choices': [{'message': {'annotations': [{'type': 'url'}, 'x']}}]}, {'choices': ['x']}]:
            self.assertIsNone(p.parsed_page_coverage(response))
        self.assertEqual(p.parsed_page_coverage(self.parsed(self.success(), ['', 'enough text on this page to count'])), (1, 2))
        no_contents = self.success()
        no_contents['choices'][0]['message']['annotations'] = [{'type': 'file', 'file': {'content': [
            {'type': 'text', 'text': 'Preamble before any page\n### Page 1\n\n### Page 2\nenough text on this page to count'}]}}]
        self.assertEqual(p.parsed_page_coverage(no_contents), (1, 2))

    def test_response_failures_retry_and_preserve_all_attempts(self):
        cases = [({'error': {'code': 503, 'message': 'busy'}}, 'generation failed'), ({'choices': [{'finish_reason': 'length'}], 'usage': {'completion_tokens': 10, 'completion_tokens_details': {'reasoning_tokens': 9}}}, 'reasoning tokens: 9'), ({'choices': [{'message': {'content': 'bad json'}}]}, 'invalid JSON'), ({'choices': [{'message': {'content': '{"effects":[{}]}'}}]}, 'Missing required field')]
        for response, message in cases:
            with self.subTest(message=message), patch.object(p, '_request_json', return_value=response) as request, self.assertRaisesRegex((ValueError, RuntimeError), message) as caught:
                p.extract_document(**self.kwargs)
            self.assertEqual(request.call_count, 3)
            self.assertEqual(len(caught.exception.provider_responses), 3)
            with patch.object(p, '_request_json', side_effect=[response, self.success()]):
                result = p.extract_document(**self.kwargs)
            self.assertEqual(result.status, 'ok')
            self.assertEqual(len(result.provider_responses), 2)

    def test_valid_json_is_ok_without_repair(self):
        with patch.object(p, '_request_json', return_value=self.success()) as request:
            result = p.extract_document(**self.kwargs)
        request.assert_called_once()
        self.assertEqual((result.status, result.review_reasons), ('ok', ()))
        self.assertEqual(result.raw_response, '{"effects":[{"claim":"finding"}]}')
        self.assertIsNone(result.repaired_response)

    def test_repairable_json_is_repaired_and_needs_review(self):
        raw = '{"effects":[{"claim":"finding"}]'
        response = {'choices': [{'message': {'content': raw}, 'finish_reason': 'length'}]}
        with patch.object(p, '_request_json', return_value=response) as request:
            result = p.extract_document(**self.kwargs)
        request.assert_called_once()
        self.sleep.assert_not_called()
        self.assertEqual(result.status, 'needs_review')
        self.assertEqual(result.review_reasons, ('JSON was repaired', 'finish reason: length'))
        self.assertEqual(result.data, {'effects': [{'claim': 'finding'}]})
        self.assertEqual((result.raw_json, result.raw_response), (raw, raw))
        self.assertEqual(json.loads(result.repaired_response), result.data)

    def test_unrepairable_json_retries_then_keeps_raw_response(self):
        bad = {'choices': [{'message': {'content': 'bad json'}}]}
        with patch.object(p, '_request_json', return_value=bad) as request, self.assertRaisesRegex(RuntimeError, 'OpenRouter returned invalid JSON') as caught:
            p.extract_document(**self.kwargs)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(caught.exception.raw_response, 'bad json')
        self.assertEqual(len(caught.exception.provider_responses), 3)

    def test_repaired_json_that_fails_validation_is_retried(self):
        invalid = {'choices': [{'message': {'content': '{"effects":[{}]'}}]}
        with patch.object(p, '_request_json', side_effect=[invalid, self.success()]) as request:
            result = p.extract_document(**self.kwargs)
        self.assertEqual(request.call_count, 2)
        self.assertEqual((result.status, result.review_reasons), ('ok', ()))
        self.assertIsNone(result.repaired_response)

    def test_embedded_error_categories(self):
        for payload, retryable in [({'error': {'code': 401}}, False), ({'choices': [{'error': {'metadata': {'error_type': 'timeout'}}}]}, True), ({'choices': [{'error': {'metadata': {'error_type': 'invalid_request'}}}]}, False), ({'choices': [{'finish_reason': 'error'}]}, True)]:
            detail, actual = p._embedded_error(payload)
            self.assertEqual(actual, retryable)
            self.assertIn('generation failed', detail)
        with patch.object(p, '_request_json', return_value={'error': {'code': 401}}) as request, self.assertRaisesRegex(RuntimeError, 'generation failed'):
            p.extract_document(**self.kwargs)
        request.assert_called_once()
        self.assertIn('unknown', p._empty_response_error({}))
