from __future__ import annotations

import argparse
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_zcurve import cli
from auto_zcurve.config import AppSettings, RunSettings


class CliSettingsTests(unittest.TestCase):
    def settings(self, argv, existing=None, interactive=False):
        args = cli.build_parser().parse_args(['run', '.', *argv])
        with patch.object(cli, 'load_run_settings', return_value=existing), patch.object(cli, 'load_app_settings', return_value=AppSettings()), patch.object(cli, 'model_request_defaults', return_value=(3, 0.2)), patch.object(cli, 'prompt_model', return_value='gemini-test') as prompt, patch.object(cli, 'validate_model_option', return_value=SimpleNamespace(name='validated')):
            result = cli._settings_from_args(args, Path('.'), Mock(), interactive, 'key')
        return result, prompt

    def test_noninteractive_requires_model(self):
        with self.assertRaisesRegex(RuntimeError, 'model is required'):
            self.settings([])

    def test_model_defaults_and_interactive_selection(self):
        result, prompt = self.settings([], interactive=True)
        self.assertEqual(result.primary_model, 'gemini-test')
        self.assertEqual(result.parallel_requests, 3)
        self.assertEqual(result.request_delay_sec, 0.2)
        prompt.assert_called_once()

    def test_saved_settings_and_explicit_overrides(self):
        saved = RunSettings(primary_model='gemini-saved', parallel_requests=7, request_delay_sec=1.2, request_timeout_sec=61, max_upload_size_mb=22)
        result, prompt = self.settings([], saved)
        self.assertEqual((result.primary_model, result.parallel_requests, result.request_timeout_sec, result.max_upload_size_mb), ('gemini-saved', 7, 61, 22))
        prompt.assert_not_called()
        result, _ = self.settings(['--model', 'gemini-new', '--parallel', '0'], saved)
        self.assertEqual((result.primary_model, result.parallel_requests), ('gemini-new', 1))
        self.assertEqual(result.request_delay_sec, 1.2)

    def test_switching_provider_does_not_reuse_old_model(self):
        saved = RunSettings(primary_model='gemini-saved')
        with self.assertRaisesRegex(RuntimeError, 'OpenRouter.*model is required'):
            self.settings(['--provider', 'openrouter'], saved)
        result, _ = self.settings(['--provider', 'openrouter', '--model', 'vendor/model'], saved)
        self.assertEqual((result.provider, result.primary_model), ('openrouter', 'validated'))


class CliCommandTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.mocks = {}
        for name in ['ensure_project_layout', 'run_preflight', 'load_run_settings', 'prompt_api_key', '_settings_from_args', 'run_project', 'retry_project', 'print_summary', 'offer_interactive_retry']:
            self.mocks[name] = self.stack.enter_context(patch.object(cli, name))
        self.mocks['ensure_project_layout'].return_value = True
        self.mocks['load_run_settings'].return_value = None
        self.mocks['prompt_api_key'].return_value = 'key'
        self.mocks['_settings_from_args'].return_value = RunSettings(primary_model='gemini-test')
        self.console = Mock()

    def test_run_declined_layout_stops_without_credentials(self):
        self.mocks['ensure_project_layout'].return_value = False
        args = cli.build_parser().parse_args(['run', '.'])
        self.assertEqual(cli.run_command(args, Path('.'), False, self.console), 0)
        self.mocks['prompt_api_key'].assert_not_called()
        self.mocks['run_project'].assert_not_called()

    def test_run_outcomes_and_option_forwarding(self):
        for summary, code in [(None, 0), (SimpleNamespace(failed_pdfs=0), 0), (SimpleNamespace(failed_pdfs=2), 1)]:
            with self.subTest(summary=summary):
                self.mocks['run_project'].return_value = summary
                args = cli.build_parser().parse_args(['run', '.', '--yes', '--force', '--skip-report', '--provider', 'openrouter', '--api-key', 'explicit'])
                self.assertEqual(cli.run_command(args, Path('.'), False, self.console), code)
                kwargs = self.mocks['run_project'].call_args.kwargs
                self.assertEqual(kwargs['project_dir'], Path.cwd())
                self.assertTrue(kwargs['force'])
                self.assertTrue(kwargs['skip_report'])
                self.assertTrue(kwargs['assume_yes'])
                self.assertFalse(kwargs['interactive'])
                self.mocks['prompt_api_key'].assert_called_with(Path.cwd(), explicit_key='explicit', interactive=False, provider='openrouter')
        self.mocks['offer_interactive_retry'].assert_not_called()

    def test_interactive_retry_updates_exit_status(self):
        args = cli.build_parser().parse_args(['run', '.', '--skip-preflight'])
        self.mocks['run_project'].return_value = SimpleNamespace(failed_pdfs=1)
        for retry, code in [(None, 1), (SimpleNamespace(failed_pdfs=0), 0)]:
            self.mocks['offer_interactive_retry'].return_value = retry
            self.assertEqual(cli.run_command(args, Path('.'), True, self.console), code)
        self.mocks['run_preflight'].assert_not_called()

    def test_retry_sources_settings_and_outcomes(self):
        for skip in [False, True]:
            for summary, code in [(None, 0), (SimpleNamespace(failed_pdfs=0), 0), (SimpleNamespace(failed_pdfs=1), 1)]:
                with self.subTest(skip=skip, summary=summary):
                    self.mocks['run_preflight'].reset_mock()
                    self.mocks['retry_project'].return_value = summary
                    self.mocks['load_run_settings'].return_value = RunSettings(primary_model='vendor/model', provider='openrouter')
                    args = cli.build_parser().parse_args(['retry', '.', '--source', 'a.pdf', '--source', 'nested/b.pdf', '--yes', '--skip-report', *(['--skip-preflight'] if skip else [])])
                    self.assertEqual(cli.retry_command(args, self.console), code)
                    self.assertEqual(self.mocks['run_preflight'].called, not skip)
                    self.mocks['prompt_api_key'].assert_called_with(Path.cwd(), explicit_key=None, interactive=False, provider='openrouter')
                    kwargs = self.mocks['retry_project'].call_args.kwargs
                    self.assertEqual(kwargs['selected_sources'], ['a.pdf', 'nested/b.pdf'])
                    self.assertTrue(kwargs['assume_yes'])
                    self.assertTrue(kwargs['skip_report'])


class CliPromptTests(unittest.TestCase):
    def test_project_prompt_widget_and_plain_input(self):
        q = Mock()
        q.path.return_value.ask.return_value = '.'
        with patch.object(cli, '_questionary', return_value=q):
            self.assertEqual(cli.prompt_project_dir(), Path.cwd())
        with patch.object(cli, '_questionary', return_value=None), patch('builtins.input', return_value=''):
            self.assertEqual(cli.prompt_project_dir(), Path.cwd())
        self.assertIsNotNone(cli._questionary())

    def test_api_key_resolution_and_prompt_fallbacks(self):
        with patch.object(cli, 'resolve_api_key', return_value='saved'), patch.object(cli, '_questionary') as questionary:
            self.assertEqual(cli.prompt_api_key(Path('.'), None, False), 'saved')
            questionary.assert_not_called()
        with patch.object(cli, 'resolve_api_key', side_effect=RuntimeError('missing')):
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                cli.prompt_api_key(Path('.'), None, False)
        q = Mock()
        q.password.return_value.ask.return_value = 'entered'
        for widget in [q, None]:
            with patch.object(cli, '_questionary', return_value=widget), patch.object(cli, 'resolve_api_key', side_effect=[RuntimeError('missing'), 'resolved']) as resolve, patch('getpass.getpass', return_value='entered'):
                self.assertEqual(cli.prompt_api_key(Path('.'), None, True), 'resolved')
                resolve.assert_called_with(Path('.'), explicit_key='entered', provider='gemini')

    def test_model_prompt_for_custom_providers(self):
        for provider in ['openrouter', 'openai_compatible']:
            with patch('builtins.input', return_value=' vendor/model '), patch.object(cli, 'validate_model_option', return_value=SimpleNamespace(name='vendor/model')) as validate:
                self.assertEqual(cli.prompt_model('key', Mock(), provider), 'vendor/model')
                validate.assert_called_once_with(provider, 'vendor/model', 'key')

    def test_gemini_live_fallback_and_plain_selection(self):
        q = Mock()
        q.select.return_value.ask.return_value = 'gemini-test'
        for live in [[SimpleNamespace(name='gemini-test')], [], RuntimeError('offline')]:
            for widget in [q, None]:
                with self.subTest(live=live, widget=bool(widget)), patch.object(cli, 'list_live_models', **({'side_effect': live} if isinstance(live, Exception) else {'return_value': live})), patch.object(cli, 'fallback_models', return_value=[SimpleNamespace(name='gemini-test')]) as fallback, patch.object(cli, '_questionary', return_value=widget), patch('builtins.input', return_value='1'):
                    self.assertEqual(cli.prompt_model('key', Mock()), 'gemini-test')
                    self.assertEqual(fallback.called, not isinstance(live, list) or not live)

    def test_retry_selection_and_cancellation(self):
        args = argparse.Namespace(skip_report=True)
        for action, selected, expected in [('no', None, False), (None, None, False), ('selected', [], False), ('selected', ['b.pdf'], True), ('all', None, True)]:
            with self.subTest(action=action, selected=selected):
                q = Mock()
                q.select.return_value.ask.return_value = action
                q.checkbox.return_value.ask.return_value = selected
                with patch.object(cli, 'load_extractions', return_value=[{'status': 'ok', 'source_name': 'a.pdf'}, {'status': 'error', 'source_name': 'b.pdf'}]), patch.object(cli, '_questionary', return_value=q), patch.object(cli, 'retry_project') as retry:
                    cli.offer_interactive_retry(args, Path('.'), Mock(), Mock(), 'key')
                    self.assertEqual(retry.called, expected)
                    if expected:
                        self.assertEqual(retry.call_args.kwargs['selected_sources'], selected)
        for answer in ['yes', 'n']:
            with patch.object(cli, 'load_extractions', return_value=[{'status': 'error'}]), patch.object(cli, '_questionary', return_value=None), patch('builtins.input', return_value=answer), patch.object(cli, 'retry_project') as retry:
                cli.offer_interactive_retry(args, Path('.'), Mock(), Mock(), 'key')
                self.assertEqual(retry.called, answer == 'yes')
        with patch.object(cli, 'load_extractions', return_value=[]), patch.object(cli, 'retry_project') as retry:
            self.assertIsNone(cli.offer_interactive_retry(args, Path('.'), Mock(), Mock(), 'key'))
            retry.assert_not_called()


class CliEntryPointTests(unittest.TestCase):
    def test_command_dispatch(self):
        for command in ['run', 'retry']:
            with patch.object(cli, command + '_command', return_value=17):
                self.assertEqual(cli.main([command, '.']), 17)
        for command in ['gui', 'tui']:
            with patch('auto_zcurve.tui.run_tui', return_value=19):
                self.assertEqual(cli.main([command]), 19)

    def test_exit_codes_and_user_messages(self):
        for error, expected in [(cli.PreflightError('Missing Python packages: rich'), 2), (KeyboardInterrupt(), 130), (RuntimeError('broken'), 1)]:
            with self.subTest(error=error), patch.object(cli, 'run_command', side_effect=error), patch.object(cli, 'CliConsole') as console:
                self.assertEqual(cli.main(['run', '.']), expected)
                if expected == 130:
                    console.return_value.warn.assert_called_once_with('Cancelled.')
                else:
                    console.return_value.error.assert_called_once()

    def test_guided_command_and_summary(self):
        with patch.object(cli, 'prompt_project_dir', return_value=Path('.')), patch.object(cli, 'run_command', return_value=7) as run:
            self.assertEqual(cli.guided(Mock()), 7)
            self.assertTrue(run.call_args.kwargs['interactive'])
        summary = SimpleNamespace(report_path=Path('/project/report.html'), successful_pdfs=2, failed_pdfs=1, extracted_effects=3, usable_zcurve_inputs=2, input_tokens=4, output_tokens=5, total_tokens=9)
        console = Mock()
        cli.print_summary(summary, console)
        self.assertIn(('Total tokens', 9), console.table.call_args.args[2])
        summary.report_path = None
        with patch.object(cli, 'format_run_result', return_value='Z-Curve Summary: values'):
            cli.print_summary(summary, console, Path('.'))
        self.assertIn(('Report', 'not rendered'), console.table.call_args.args[2])
        console.print.assert_called_with('values')
