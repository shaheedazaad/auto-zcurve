import io
import json
import threading
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from auto_zcurve import openai_compatible as p


class EndpointFailureTests(unittest.TestCase):
    def call(self, **kwargs):
        return p._call(**dict(dict(model='local', api_key='secret', prompt='extract', base_url='http://localhost/v1', response_schema={}), **kwargs))

    def response(self, body=None):
        return io.BytesIO(json.dumps(body if body is not None else {'choices': [{'message': {'content': '{}'}}]}).encode())

    def test_invalid_requests_never_contact_endpoint(self):
        for kwargs, message in [({'model': ' '}, 'model ID'), ({'base_url': 'bad'}, 'HTTP'), ({'response_format': 'bad'}, 'Unknown JSON')]:
            with self.subTest(kwargs=kwargs), patch.object(p.urllib.request, 'urlopen') as request, self.assertRaisesRegex(p.ProviderError, message):
                self.call(**kwargs)
            request.assert_not_called()
        self.assertTrue(p.check_model('model', base_url='bad'))
        self.assertTrue(p.check_model(' ', base_url='http://localhost'))
        self.assertEqual(p.check_model('model', base_url='http://localhost'), [])
        request = p._request('http://localhost', api_key='')
        self.assertIsNone(request.data)
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(p._redact('detail', ''), 'detail')

    def test_malformed_response_shapes_preserve_raw_body(self):
        cases = [([], 'invalid response object'), ({}, 'no choices'), ({'choices': 'bad'}, 'no choices'), ({'choices': [1]}, 'invalid choice'), ({'choices': [{'message': 'bad'}]}, 'invalid message'), ({'choices': [{'message': {'content': ' '}}]}, 'no message content')]
        for body, message in cases:
            with self.subTest(body=body), patch.object(p.urllib.request, 'urlopen', return_value=self.response(body)), self.assertRaisesRegex(p.ProviderError, message) as caught:
                self.call()
            self.assertEqual(json.loads(caught.exception.raw_response), body)
        for raw in [b'invalid', b'\xff']:
            with patch.object(p.urllib.request, 'urlopen', return_value=io.BytesIO(raw)), self.assertRaisesRegex(p.ProviderError, 'invalid JSON'):
                self.call()

    def test_usage_reasoning_and_timeout_forwarding(self):
        for usage, expected in [({'prompt_tokens': 4, 'completion_tokens': 2}, {'input_tokens': 4, 'output_tokens': 2}), ('bad', {'input_tokens': None, 'output_tokens': None})]:
            with patch.object(p.urllib.request, 'urlopen', return_value=self.response({'choices': [{'message': {'content': '{}'}}], 'usage': usage})) as request:
                text, tokens = self.call(reasoning_effort='high', timeout_sec=12)
            self.assertEqual((text, tokens), ('{}', expected))
            self.assertEqual(request.call_args.kwargs['timeout'], 12)
            self.assertEqual(json.loads(request.call_args.args[0].data)['reasoning_effort'], 'high')

    def test_http_retry_then_success_and_exhaustion(self):
        def error(code):
            return urllib.error.HTTPError('http://localhost', code, 'failure', {}, io.BytesIO(b'secret details'))
        for code in [401, 429]:
            event = Mock()
            event.is_set.return_value = False
            event.wait.return_value = False
            errors = [error(code) for _ in range(3)]
            with patch.object(p.urllib.request, 'urlopen', side_effect=errors) as request, self.assertRaisesRegex(p.ProviderError, 'redacted') as caught:
                self.call(cancel_event=event)
            self.assertNotIn('secret', str(caught.exception))
            self.assertEqual(request.call_count, 1 if code == 401 else 3)
        event.reset_mock()
        with patch.object(p.urllib.request, 'urlopen', side_effect=[error(503), self.response()]) as request:
            self.assertEqual(self.call(cancel_event=event)[0], '{}')
            self.assertEqual(request.call_count, 2)
            event.wait.assert_called_once_with(2.0)

    def test_network_retries_redact_secrets(self):
        event = Mock()
        event.is_set.return_value = False
        event.wait.return_value = False
        with patch.object(p.urllib.request, 'urlopen', side_effect=urllib.error.URLError('secret unavailable')) as request, self.assertRaisesRegex(p.ProviderError, 'redacted'):
            self.call(cancel_event=event)
        self.assertEqual(request.call_count, 3)
        self.assertEqual([call.args[0] for call in event.wait.call_args_list], [2.0, 4.0])

    def test_cancellation_before_request_during_error_and_during_backoff(self):
        event = threading.Event()
        event.set()
        with patch.object(p.urllib.request, 'urlopen') as request, self.assertRaises(p.ExtractionCancelled):
            self.call(cancel_event=event)
        request.assert_not_called()
        for error in [OSError('network'), urllib.error.HTTPError('http://localhost', 503, 'failure', {}, io.BytesIO())]:
            event.clear()
            def cancel(*args, **kwargs):
                event.set()
                raise error
            with patch.object(p.urllib.request, 'urlopen', side_effect=cancel), self.assertRaises(p.ExtractionCancelled):
                self.call(cancel_event=event)
        event = Mock()
        event.is_set.return_value = False
        event.wait.return_value = True
        with patch.object(p.urllib.request, 'urlopen', side_effect=OSError('offline')), self.assertRaises(p.ExtractionCancelled):
            self.call(cancel_event=event)

    def test_local_pdf_text_pages_errors_and_cancellation(self):
        pages = [Mock(), Mock(), Mock()]
        for page, text in zip(pages, [' first ', None, 'third']):
            page.extract_text.return_value = text
        with patch('pypdf.PdfReader', return_value=NS(pages=pages)):
            self.assertEqual(p.pdf_text(Path('a.pdf'), None), '[Page 1]\nfirst\n\n[Page 3]\nthird')
            event = threading.Event()
            event.set()
            with self.assertRaises(p.ExtractionCancelled):
                p.pdf_text(Path('a.pdf'), event)
        with patch('pypdf.PdfReader', side_effect=ValueError('damaged')), self.assertRaisesRegex(p.ProviderError, 'Could not extract PDF text: damaged'):
            p.pdf_text(Path('a.pdf'), None)
