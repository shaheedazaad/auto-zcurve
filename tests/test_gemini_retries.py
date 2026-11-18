from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import httpx
from google.genai import errors

from auto_zcurve import gemini
from auto_zcurve.openai_compatible import ExtractionCancelled


def api_error(code: int, headers: dict[str, str] | None = None) -> errors.APIError:
    response = httpx.Response(code, headers=headers or {})
    error_class = errors.ServerError if code >= 500 else errors.ClientError
    return error_class(code, {'error': {'code': code, 'message': 'boom', 'status': 'X'}}, response)


class GeminiRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'article.pdf'
        self.source.write_bytes(b'%PDF-test')
        self.client = Mock()
        self.client.files.upload.return_value = 'uploaded'
        self.generated = ({'effects': []}, '{"effects":[]}', None, {})
        self.waits: list[float] = []

    def extract(self, *, generate=None, cancellation_event=None):
        generate = generate or Mock(return_value=self.generated)
        wait = Mock(side_effect=lambda delay, event: self.waits.append(delay))
        with patch('google.genai.Client', return_value=self.client), \
                patch.object(gemini, 'build_system_prompt', return_value='prompt'), \
                patch.object(gemini, '_generate', generate), \
                patch.object(gemini, 'validate_extracted_json'), \
                patch.object(gemini, '_wait_for_retry', wait):
            result = gemini.extract_pdf(
                source_path=self.source, source_name='article.pdf', api_key='key',
                primary_model='gemini-test', response_schema={}, schema_config=object(),
                instruction_path=Path(self.tmp.name) / 'instructions.md',
                cancellation_event=cancellation_event,
            )
        return result, generate

    def test_transient_upload_error_succeeds_on_retry_with_a_fresh_file_read(self):
        reads = []

        def upload(*, file, config):
            reads.append(file.read())
            if len(reads) == 1:
                raise api_error(503)
            return 'uploaded'

        self.client.files.upload.side_effect = upload
        result, generate = self.extract()
        self.assertEqual(result.status, 'ok')
        self.assertEqual(reads, [b'%PDF-test', b'%PDF-test'])
        self.assertEqual(self.waits, [1.0])
        self.assertEqual(generate.call_args.args[1], 'uploaded')

    def test_transient_generate_error_honours_retry_after(self):
        generate = Mock(side_effect=[api_error(429, {'Retry-After': '2'}), self.generated])
        result, _ = self.extract(generate=generate)
        self.assertEqual(result.status, 'ok')
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(self.waits, [2.0])

    def test_transient_network_error_is_retried(self):
        generate = Mock(side_effect=[httpx.ConnectError('offline'), httpx.ReadTimeout('slow'), self.generated])
        result, _ = self.extract(generate=generate)
        self.assertEqual(result.status, 'ok')
        self.assertEqual(self.waits, [1.0, 2.0])

    def test_transient_error_stops_after_three_attempts(self):
        generate = Mock(side_effect=[api_error(500), api_error(502), api_error(503)])
        with self.assertRaises(errors.ServerError) as caught:
            self.extract(generate=generate)
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(generate.call_count, gemini.MAX_REQUEST_ATTEMPTS)
        self.assertEqual(gemini.MAX_REQUEST_ATTEMPTS, 3)
        self.assertEqual(self.waits, [1.0, 2.0])

    def test_permanent_errors_are_not_retried(self):
        for error in [api_error(400), api_error(403), RuntimeError('Gemini returned an empty text payload.')]:
            with self.subTest(error=error):
                self.waits.clear()
                generate = Mock(side_effect=error)
                with self.assertRaises(type(error)):
                    self.extract(generate=generate)
                self.assertEqual(generate.call_count, 1)
                self.assertEqual(self.waits, [])
        self.client.files.upload.reset_mock()
        self.client.files.upload.side_effect = api_error(404)
        generate = Mock()
        with self.assertRaises(errors.ClientError):
            self.extract(generate=generate)
        self.assertEqual(self.client.files.upload.call_count, 1)
        generate.assert_not_called()

    def test_cancellation_during_backoff_stops_further_attempts(self):
        cancellation = threading.Event()
        generate = Mock(side_effect=api_error(503))
        real_wait = gemini._wait_for_retry

        def wait(delay, event):
            cancellation.set()
            real_wait(delay, event)

        with patch('google.genai.Client', return_value=self.client), \
                patch.object(gemini, 'build_system_prompt', return_value='prompt'), \
                patch.object(gemini, '_generate', generate), \
                patch.object(gemini, '_wait_for_retry', side_effect=wait), \
                self.assertRaises(ExtractionCancelled):
            gemini.extract_pdf(
                source_path=self.source, source_name='article.pdf', api_key='key',
                primary_model='gemini-test', response_schema={}, schema_config=object(),
                instruction_path=Path(self.tmp.name) / 'instructions.md',
                cancellation_event=cancellation,
            )
        self.assertEqual(generate.call_count, 1)

    def test_cancelled_run_makes_no_gemini_request(self):
        cancellation = threading.Event()
        cancellation.set()
        generate = Mock()
        with self.assertRaises(ExtractionCancelled):
            self.extract(generate=generate, cancellation_event=cancellation)
        self.client.files.upload.assert_not_called()
        generate.assert_not_called()

    def test_wait_for_retry_sleeps_in_short_slices_until_the_delay_elapses(self):
        clock = iter([10.0, 10.0, 10.2, 10.25])
        with patch.object(gemini.time, 'monotonic', side_effect=lambda: next(clock)), \
                patch.object(gemini.time, 'sleep') as sleep:
            gemini._wait_for_retry(0.25, NS(is_set=lambda: False))
        self.assertEqual(sleep.call_count, 2)
        self.assertAlmostEqual(sleep.call_args_list[0].args[0], 0.1)
        self.assertAlmostEqual(sleep.call_args_list[1].args[0], 0.05)
        with patch.object(gemini.time, 'sleep') as sleep:
            gemini._wait_for_retry(0, None)
        sleep.assert_not_called()

    def test_retry_after_parsing(self):
        self.assertEqual(gemini._retry_after_sec(api_error(429, {'Retry-After': '7'})), 7.0)
        self.assertIsNone(gemini._retry_after_sec(api_error(429, {'Retry-After': 'Wed, 21 Oct 2015 07:28:00 GMT'})))
        self.assertIsNone(gemini._retry_after_sec(api_error(429)))
        self.assertIsNone(gemini._retry_after_sec(RuntimeError('no response')))

    def test_long_retry_after_is_capped(self):
        generate = Mock(side_effect=[api_error(429, {'Retry-After': '90'}), self.generated])
        self.extract(generate=generate)
        self.assertEqual(self.waits, [30.0])


class RunCancellationPlumbingTests(unittest.TestCase):
    def test_runner_cancellation_reaches_the_gemini_adapter(self):
        from auto_zcurve import providers

        implementation = Mock(return_value=object())
        cancellation = threading.Event()
        with patch.object(providers.importlib, 'import_module', return_value=NS(extract_pdf=implementation)):
            providers.extract_pdf(
                provider='gemini', source_path=Path('a.pdf'), source_name='a.pdf', api_key='k',
                primary_model='m', response_schema={}, schema_config=object(),
                instruction_path=Path('i.md'), request_timeout_sec=30,
                cancellation_event=cancellation,
            )
        self.assertIs(implementation.call_args.kwargs['cancellation_event'], cancellation)

    def test_runner_passes_its_cancellation_signal_and_reports_a_cancelled_run(self):
        from auto_zcurve import runner
        from auto_zcurve.config import RunSettings
        from auto_zcurve.models import ModelOption

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / 'sources').mkdir()
            (project / 'extraction_schema.yml').write_text('effects: {claim: {type: string}}')
            source = project / 'sources/a.pdf'
            source.write_bytes(b'%PDF')
            cancellation = threading.Event()

            def cancelled_mid_retry(**kwargs):
                cancellation.set()
                raise ExtractionCancelled('Extraction cancelled by user.')

            with patch.object(runner, 'extract_pdf', side_effect=cancelled_mid_retry) as extract, \
                    self.assertRaises(runner.RunCancelled):
                runner._process_one(
                    project_dir=project, source_path=source,
                    settings=RunSettings(primary_model='test', request_delay_sec=0),
                    api_key='key', retry=False, run_id='run',
                    model_option=ModelOption('test', 'Test'),
                    cancellation_event=cancellation,
                )
        self.assertIs(extract.call_args.kwargs['cancellation_event'], cancellation)


if __name__ == '__main__':
    unittest.main()
