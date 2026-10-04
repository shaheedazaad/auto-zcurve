import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from auto_zcurve import runner as r
from auto_zcurve.artifacts import save_extractions, load_extractions, load_run_log
from auto_zcurve.config import RunSettings, save_run_settings
from auto_zcurve.models import ModelOption
from auto_zcurve.llm import ExtractionResult


class Console(Mock):
    @contextmanager
    def progress(self, *args):
        yield Mock()


class RunnerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / 'sources').mkdir()
        (self.project / 'extraction_schema.yml').write_text('effects: {claim: {type: string}}')
        self.source = self.project / 'sources/a.pdf'
        self.source.write_bytes(b'%PDF')
        self.settings = RunSettings(primary_model='test', request_delay_sec=0)
        self.console = Console()
        self.option = ModelOption('test', 'Test', supports_reasoning=True)

    def run_project(self, **kwargs):
        return r.run_project(**dict(dict(project_dir=self.project, settings=self.settings, assume_yes=True, interactive=False, force=False, skip_report=True, console=self.console, api_key='secret'), **kwargs))

    def retry(self, **kwargs):
        return r.retry_project(**dict(dict(project_dir=self.project, settings=self.settings, selected_sources=None, assume_yes=True, skip_report=True, console=self.console, api_key='secret'), **kwargs))

    def process(self, **kwargs):
        return r._process_one(**dict(dict(project_dir=self.project, source_path=self.source, settings=self.settings, api_key='secret', retry=False, run_id='run', model_option=self.option), **kwargs))

    def extraction(self, **kwargs):
        return ExtractionResult(kwargs['source_path'], kwargs['source_name'], 'ok', data={'effects': [{'claim': 'finding'}]}, input_tokens=2, output_tokens=3, total_tokens=5)

    def test_setup_missing_sources_and_schema_consent(self):
        project = self.project / 'new'
        self.assertFalse(r.ensure_project_layout(project, True, False, self.console))
        self.assertTrue((project / 'sources').is_dir())
        self.assertTrue(r.ensure_project_layout(project, True, False, self.console))
        self.console.default_schema_created.assert_called_once_with(project / 'extraction_schema.yml')
        for existing_sources in [False, True]:
            other = self.project / str(existing_sources)
            if existing_sources:
                (other / 'sources').mkdir(parents=True)
            with patch.object(r, '_confirm', return_value=False), self.assertRaisesRegex(RuntimeError, 'required'):
                r.ensure_project_layout(other, False, True, self.console)
        plain = Mock(spec=['warn', 'info'])
        (project / 'extraction_schema.yml').unlink()
        self.assertTrue(r.ensure_project_layout(project, True, False, plain))

    def test_confirmation_widget_and_fallback(self):
        self.assertTrue(r._confirm('question', True, False))
        with patch('questionary.confirm') as confirm:
            confirm.return_value.ask.return_value = False
            self.assertFalse(r._confirm('question', True, True))
        for answer, default, expected in [('', True, True), ('', False, False), ('yes', False, True), ('no', True, False)]:
            with patch('questionary.confirm', side_effect=RuntimeError()), patch('builtins.input', return_value=answer):
                self.assertEqual(r._confirm('question', default, True), expected)

    def test_pacer_waits_and_honors_cancellation(self):
        pacer = r._RequestPacer(1)
        with patch.object(r.time, 'monotonic', side_effect=[0, 0.5, 1]), patch.object(r.time, 'sleep') as sleep:
            pacer.wait(None)
            pacer.wait(None)
        sleep.assert_called_once_with(0.1)
        event = threading.Event()
        event.set()
        with self.assertRaises(r.RunCancelled):
            pacer.wait(event)

    def test_oversized_file_is_recorded_without_provider_call(self):
        self.settings.max_upload_size_mb = 0
        with patch.object(r, 'extract_pdf') as extract:
            result = self.process()
        self.assertEqual(result.status, 'error')
        self.assertIn('max_upload_size_mb', result.error)
        extract.assert_not_called()
        self.assertEqual(load_run_log(self.project)[0]['attempt'], 1)
        self.assertEqual(load_extractions(self.project)[0]['status'], 'error')

    def test_provider_failure_retains_diagnostics_and_redacts_error(self):
        error = RuntimeError('request secret failed')
        error.diagnostics = {'parser_name': 'parser', 'parser_version': '1', 'parser_config_version': '2', 'source_sha256': 'source', 'document_sha256': 'doc', 'page_count': 2, 'mean_grade': 'A', 'low_grade': 'B', 'warnings': ['warn'], 'cache_metadata_path': 'cache', 'duration_sec': 1.5}
        error.raw_response = 'raw'
        error.repaired_response = 'repair'
        error.provider_responses = [{'attempt': 1}]
        with patch.object(r, 'extract_pdf', side_effect=error):
            result = self.process()
        self.assertEqual(result.error, 'request [redacted] failed')
        self.assertEqual((result.parser_name, result.parser_page_count, result.parser_warnings, result.parser_duration_sec), ('parser', 2, ('warn',), 1.5))
        self.assertEqual(result.raw_response, 'raw')
        with patch.object(r, 'extract_pdf', side_effect=r.RunCancelled('cancelled')), self.assertRaises(r.RunCancelled):
            self.process()
        self.assertEqual(len(load_run_log(self.project)), 1)

    def test_no_work_and_layout_incomplete_return_without_provider(self):
        with patch.object(r, 'ensure_project_layout', return_value=False):
            self.assertIsNone(self.run_project())
            self.assertIsNone(self.retry())
        self.source.unlink()
        self.assertIsNone(self.run_project())

    def test_changed_model_reprocesses_successes_and_report_paths(self):
        save_extractions(self.project, [{'source_name': 'a.pdf', 'status': 'ok'}])
        for report in [self.project / 'output/report.html', self.project.parent / 'outside.html']:
            save_run_settings(self.project, RunSettings(primary_model='old'))
            with patch.object(r, 'validate_model_option', return_value=self.option), patch.object(r, 'extract_pdf', side_effect=self.extraction) as extract, patch.object(r, 'render_report', return_value=report):
                summary = self.run_project(skip_report=False, force=False)
            self.assertEqual(summary.report_path, report)
            self.assertEqual(summary.total_tokens, 5)
            # The saved model changed, so the existing successful PDF is reprocessed.
            extract.assert_called_once()
        with patch.object(r, 'validate_model_option', return_value=self.option), patch.object(r, '_process_paths') as process:
            self.run_project()
        process.assert_not_called()

    def test_retry_selected_failures_and_report_regeneration(self):
        for report in [self.project / 'output/report.html', self.project.parent / 'outside.html']:
            save_extractions(self.project, [{'source_name': 'a.pdf', 'status': 'error'}, {'source_name': 'b.pdf', 'status': 'error'}])
            with patch.object(r, 'validate_model_option', return_value=self.option), patch.object(r, 'extract_pdf', side_effect=self.extraction) as extract, patch.object(r, 'render_report', return_value=report):
                summary = self.retry(selected_sources=['a.pdf'], skip_report=False)
            self.assertEqual(summary.failed_pdfs, 1)
            self.assertEqual(summary.report_path, report)
            extract.assert_called_once()
            self.assertTrue(load_run_log(self.project)[-1]['retry'])
        for exists in [False, True]:
            save_extractions(self.project, [])
            report = self.project / 'output/report.html'
            if exists:
                report.touch()
            summary = self.retry()
            self.assertEqual(summary.report_path, report if exists else None)

    def test_regenerate_report_and_cancellation(self):
        event = threading.Event()
        report = self.project / 'output/report.html'
        kwargs = dict(project_dir=self.project, settings=self.settings, console=self.console, cancellation_event=event)
        with patch.object(r, 'render_report', return_value=report):
            self.assertEqual(r.regenerate_report(**kwargs).report_path, report)
        event.set()
        with self.assertRaises(r.RunCancelled):
            r.regenerate_report(**kwargs)
        event.clear()
        def cancel(**kwargs):
            event.set()
            return report
        with patch.object(r, 'render_report', side_effect=cancel), self.assertRaises(r.RunCancelled):
            r.regenerate_report(**kwargs)

    def test_worker_propagates_unexpected_failure(self):
        with patch.object(r, '_process_one', side_effect=ValueError('bad schema')), self.assertRaisesRegex(ValueError, 'bad schema'):
            r._process_paths(paths=[self.source], workers=1, project_dir=self.project, settings=self.settings, api_key='secret', retry=False, run_id='run', model_option=self.option, progress=Mock(), cancellation_event=None)

        class SynchronousThread:
            def __init__(self, *, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        with patch.object(r.threading, 'Thread', SynchronousThread), patch.object(r, '_process_one', side_effect=ValueError('bad schema')), self.assertRaisesRegex(ValueError, 'bad schema'):
            r._process_paths(paths=[self.source, self.source], workers=2, project_dir=self.project, settings=self.settings, api_key='secret', retry=False, run_id='run', model_option=self.option, progress=Mock(), cancellation_event=None)

    def test_retry_can_skip_report_after_processing(self):
        save_extractions(self.project, [{'source_name': 'a.pdf', 'status': 'error'}])
        with patch.object(r, 'validate_model_option', return_value=self.option), patch.object(r, 'extract_pdf', side_effect=self.extraction), patch.object(r, 'render_report') as render:
            summary = self.retry(skip_report=True)
        self.assertEqual(summary.successful_pdfs, 1)
        self.assertIsNone(summary.report_path)
        render.assert_not_called()

    def test_worker_checks_cancellation_after_dequeuing(self):
        event = threading.Event()
        original_queue = r.queue.Queue
        class CancellingQueue(original_queue):
            def get_nowait(self):
                path = super().get_nowait()
                event.set()
                return path
        with patch.object(r.queue, 'Queue', CancellingQueue), patch.object(r, '_process_one') as process, self.assertRaises(r.RunCancelled):
            r._process_paths(paths=[self.source], workers=1, project_dir=self.project, settings=self.settings, api_key='secret', retry=False, run_id='run', model_option=self.option, progress=Mock(), cancellation_event=event)
        process.assert_not_called()

    def test_worker_stops_before_taking_another_path_when_cancelled(self):
        event = threading.Event()
        def process(**kwargs):
            event.set()
            return self.extraction(**kwargs)
        class SynchronousThread:
            def __init__(self, *, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        with patch.object(r.threading, 'Thread', SynchronousThread), patch.object(r, '_process_one', side_effect=process) as extract, self.assertRaises(r.RunCancelled):
            r._process_paths(paths=[self.source, self.source], workers=1, project_dir=self.project, settings=self.settings, api_key='secret', retry=False, run_id='run', model_option=self.option, progress=Mock(), cancellation_event=event)
        extract.assert_called_once()
