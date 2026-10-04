import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_zcurve import user_facing as u


class ReadinessTests(unittest.TestCase):
    def test_dependency_inventory_is_combined(self):
        with patch.object(u, 'missing_python_dependencies', return_value=['python']), patch.object(u, 'check_system_tools', return_value=['R']), patch.object(u, 'check_r_packages', return_value=['zcurve']):
            self.assertEqual(u.default_dependency_check(), (['python'], ['R'], ['zcurve']))

    def test_missing_project_model_and_key_are_reported_without_keychain_access(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), patch.object(u, 'load_dotenv'), patch.object(u, 'load_saved_api_key') as keychain:
            result = u.check_project_readiness(Path(tmp) / 'missing', api_key=None, model=None, dependency_check=lambda: ([], [], []))
            self.assertFalse(result.ready)
            self.assertEqual([issue.key for issue in result.issues], ['project_missing', 'api_key_missing', 'model_missing'])
            self.assertEqual(result.next_action, result.issues[0].next_action)
            keychain.assert_not_called()

    def test_all_dependency_failures_and_missing_schema(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(u, 'DEFAULT_SCHEMA', Path(tmp) / 'missing-schema'):
            result = u.check_project_readiness(Path(tmp), api_key='key', model='model', dependency_check=lambda: (['rich'], ['Quarto', 'R'], ['zcurve']))
            self.assertEqual([issue.key for issue in result.issues], ['sources_missing', 'schema_missing', 'python_deps_missing', 'quarto_missing', 'system_tools_missing', 'r_deps_missing'])
            self.assertIn('rich', result.issues[2].explanation)
            self.assertIn('R', result.issues[4].explanation)
            self.assertIn('zcurve', result.issues[5].explanation)

    def test_preflight_exception_is_explained(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = u.check_project_readiness(Path(tmp), api_key='key', model='model', dependency_check=Mock(side_effect=RuntimeError('probe failed')))
            self.assertFalse(result.ready)
            self.assertEqual(result.issues[-1].key, 'preflight_failed')
            self.assertIn('probe failed', result.issues[-1].next_action)

    def test_ready_from_environment_counts_only_files(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'GEMINI_API_KEY': 'key'}, clear=True), patch.object(u, 'load_dotenv'):
            project = Path(tmp)
            (project / 'sources/nested').mkdir(parents=True)
            (project / 'sources/a.pdf').touch()
            (project / 'sources/nested/b.pdf').touch()
            (project / 'sources/directory.pdf').mkdir()
            result = u.check_project_readiness(project, api_key=None, model='model', dependency_check=lambda: ([], [], []))
            self.assertTrue(result.ready)
            self.assertEqual(result.pdf_count, 2)
            self.assertEqual(result.issues, ())
            self.assertEqual(result.next_action, 'Ready to run extraction.')


class ErrorPresentationTests(unittest.TestCase):
    def test_error_categories_preserve_detail_and_give_next_action(self):
        cases = [('OPENROUTER_API_KEY required', 'OpenRouter API key missing'), ('OpenRouter API key missing', 'OpenRouter API key missing'), ('GEMINI_API_KEY required', 'Gemini API key missing'), ('OpenRouter 403', 'OpenRouter rejected the API key'), ('invalid api key', 'Gemini rejected the API key'), ('maximum size exceeded', 'PDF too large'), ('Missing system tools: Quarto', 'Quarto missing'), ('Missing R packages: zcurve', 'Missing dependency'), ('Quarto is not installed', 'Quarto missing'), ('report rendering failed', 'Report could not be created'), ('schema JSON required', 'Extraction schema problem'), ('OpenRouter request failed', 'OpenRouter request failed'), ('network timeout', 'Gemini request failed'), ('no usable statistics', 'No usable statistics found'), ('unexpected failure', 'Run failed')]
        for detail, title in cases:
            with self.subTest(detail=detail):
                error = u.classify_error(RuntimeError(detail))
                self.assertEqual(error.title, title)
                self.assertEqual(error.technical_detail, detail)
                self.assertTrue(error.explanation)
                self.assertTrue(error.next_action)
                self.assertEqual(error.compact(), f'{title}: {error.explanation} {error.next_action}')

    def test_failed_pdf_rows_skip_success_and_apply_limit(self):
        records = [{'source_name': 'ok.pdf', 'status': 'ok'}, {'source_name': 'bad.pdf', 'status': 'error', 'error': 'too large'}, {'status': 'failed'}]
        with patch.object(u, 'load_extractions', return_value=records):
            self.assertEqual(u.failed_pdf_rows(Path('.'), limit=1), [('bad.pdf', u.classify_error('too large').compact())])
            self.assertEqual(u.failed_pdf_rows(Path('.'))[-1], ('', u.classify_error('').compact()))
            self.assertEqual(u.failed_pdf_rows(Path('.'), limit=0), [])

    def test_result_paths_and_metrics(self):
        summary = SimpleNamespace(report_path=None, successful_pdfs=2, failed_pdfs=1, extracted_effects=4, usable_zcurve_inputs=3, input_tokens=10, output_tokens=5, total_tokens=15)
        for report, expected in [(None, 'not rendered'), (Path('/project/output/report.html'), 'output/report.html'), (Path('/elsewhere/report.html'), '/elsewhere/report.html')]:
            summary.report_path = report
            result = u.format_run_result(summary, Path('/project'))
            self.assertEqual(result.splitlines()[0], 'Report: ' + expected)
            self.assertIn('Successful PDFs: 2', result)
            self.assertIn('Total tokens: 15', result)

    def test_open_report_missing_file_and_missing_platform_opener(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / 'report.html'
            with self.assertRaises(FileNotFoundError):
                u.open_report_path(report, popen=Mock())
            report.touch()
            with patch.object(u.os, 'startfile', None, create=True), self.assertRaisesRegex(RuntimeError, 'No Windows report opener'):
                u.open_report_path(report, system='Windows')
            with patch.object(u, 'report_opener_command', return_value=None), self.assertRaisesRegex(RuntimeError, 'No report opener'):
                u.open_report_path(report, system='Linux', popen=Mock())
            opener = Mock()
            with patch.object(u.platform, 'system', return_value='Darwin'):
                u.open_report_path(report, popen=opener)
            opener.assert_called_once_with(['open', str(report.resolve())])
