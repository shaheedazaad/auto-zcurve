from __future__ import annotations

import argparse
import io
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_zcurve import cli, credentials
from auto_zcurve.config import RunSettings, load_selected_extractor_id
from auto_zcurve.credentials import KeyVault
from auto_zcurve.extractors import create_extractor, get_extractor, set_key_saved
from tests.test_key_vault import FakeKeyring


class CliExtractorTests(unittest.TestCase):
    """Choosing an extractor and its key from the command line."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        self.keyring = FakeKeyring()
        for item in (
            patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name}),
            patch.object(credentials, "_keyring_module", side_effect=lambda: self.keyring),
        ):
            item.start()
            self.addCleanup(item.stop)

    def args(self, *argv):
        return cli.build_parser().parse_args(["run", str(self.project), *argv])

    def test_named_extractor_runs_with_its_settings_and_key(self):
        lab = create_extractor({"name": "Lab Gemini", "provider": "gemini", "model": "gemini-x", "parallel_requests": "3"})
        KeyVault().set(lab.id, "lab-key")
        set_key_saved(lab.id, True)

        settings, key = cli._run_inputs(self.args("--extractor", "lab gemini"), self.project, False, Mock())

        self.assertEqual((settings.primary_model, settings.parallel_requests, settings.extractor_id), ("gemini-x", 3, lab.id))
        self.assertEqual(key, "lab-key")
        self.assertEqual(load_selected_extractor_id(self.project), lab.id)
        settings, _ = cli._run_inputs(self.args(), self.project, False, Mock())
        self.assertEqual(settings.extractor_id, lab.id)

    def test_unknown_or_missing_extractor_lists_the_choices(self):
        with self.assertRaisesRegex(RuntimeError, "No extractors exist yet"):
            cli._run_inputs(self.args(), self.project, False, Mock())
        create_extractor({"name": "Lab", "provider": "gemini", "model": "m"})
        with self.assertRaisesRegex(RuntimeError, "--extractor NAME. Available extractors: “Lab”"):
            cli._run_inputs(self.args(), self.project, False, Mock())
        with self.assertRaisesRegex(RuntimeError, "No extractor is called “Other”. Available extractors: “Lab”"):
            cli._run_inputs(self.args("--extractor", "Other"), self.project, False, Mock())

    def test_keys_come_only_from_the_vault(self):
        create_extractor({"name": "Lab", "provider": "gemini", "model": "m"})
        with patch.dict(os.environ, {"GEMINI_API_KEY": "from-environment"}):
            (self.project / ".env").write_text("GEMINI_API_KEY=from-dotenv\n")
            with self.assertRaisesRegex(RuntimeError, "has no API key. Add one in the browser app"):
                cli._run_inputs(self.args("--extractor", "Lab"), self.project, False, Mock())
        local = create_extractor({"name": "Local", "provider": "openai_compatible", "model": "m", "base_url": "http://h/v1"})
        self.assertEqual(cli.extractor_key(local), "")
        self.assertEqual(self.keyring.reads, 0)

    def test_an_unreadable_vault_is_reported(self):
        lab = create_extractor({"name": "Lab", "provider": "gemini", "model": "m"})
        set_key_saved(lab.id, True)
        self.keyring.items[("auto-zcurve", "extractor_keys")] = "{broken"
        with self.assertRaisesRegex(RuntimeError, "Couldn't unlock saved keys"):
            cli.extractor_key(get_extractor(lab.id))

    def test_interactive_runs_ask_which_extractor_to_use(self):
        first = create_extractor({"name": "A", "provider": "gemini", "model": "m"})
        second = create_extractor({"name": "B", "provider": "gemini", "model": "n"})
        q = Mock()
        q.select.return_value.ask.return_value = second.id
        with patch.object(cli, "_questionary", return_value=q):
            self.assertEqual(cli.choose_extractor(None, self.project, True, Mock()), second)
        console = Mock()
        for answer, expected in [("1", first), ("9", None), ("x", None)]:
            with self.subTest(answer=answer), patch.object(cli, "_questionary", return_value=None), patch("builtins.input", return_value=answer):
                self.assertEqual(cli.prompt_extractor(console), expected)
        console.table.assert_called()

    def test_prompt_without_extractors_returns_nothing(self):
        self.assertIsNone(cli.prompt_extractor(Mock()))

    def test_listing_extractors(self):
        console = Mock()
        self.assertEqual(cli.list_extractors_command(console), 0)
        console.info.assert_called_once()
        lab = create_extractor({"name": "Lab", "provider": "gemini", "model": "m"})
        set_key_saved(lab.id, True)
        create_extractor({"name": "Local", "provider": "openai_compatible", "model": "q", "base_url": "http://h/v1"})
        create_extractor({"name": "Router", "provider": "openrouter", "model": "v/m"})
        self.assertEqual(cli.list_extractors_command(console), 0)
        rows = console.table.call_args.args[2]
        self.assertEqual([row[3] for row in rows], ["saved", "not set (optional)", "not set"])
        with patch.object(cli, "CliConsole", return_value=Mock()):
            self.assertEqual(cli.main(["extractors"]), 0)

    def test_run_and_retry_never_print_the_extractors_key(self):
        sentinel = "sk-sentinel-4f9c2e"
        lab = create_extractor({"name": "Lab", "provider": "gemini", "model": "m"})
        KeyVault().set(lab.id, sentinel)
        set_key_saved(lab.id, True)
        (self.project / "sources").mkdir()

        def leak(**kwargs):
            console = kwargs["console"]
            console.info(f"Using {sentinel}")
            console.warn(f"Provider echoed {sentinel}")
            console.table("Echo", ["Key"], [[sentinel]])
            # This error's compact message echoes its text, so the key would reach cli.main's output.
            raise RuntimeError(f"Prompt for {sentinel} has 9 tokens; this model accepts 5.")

        for command, runner in [("run", "run_project"), ("retry", "retry_project")]:
            out, err = io.StringIO(), io.StringIO()
            with self.subTest(command=command), patch.object(cli, runner, side_effect=leak), redirect_stdout(out), redirect_stderr(err):
                code = cli.main([command, str(self.project), "--extractor", "Lab", "--yes", "--skip-preflight"])
            self.assertEqual(code, 1)
            self.assertIn("Prompt for [redacted] has 9 tokens", out.getvalue())
            self.assertNotIn(sentinel, out.getvalue() + err.getvalue())


class CliCommandTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.mocks = {}
        for name in ['ensure_project_layout', 'run_preflight', '_run_inputs', 'run_project', 'retry_project', 'print_summary', 'offer_interactive_retry']:
            self.mocks[name] = self.stack.enter_context(patch.object(cli, name))
        self.mocks['ensure_project_layout'].return_value = True
        self.mocks['_run_inputs'].return_value = (RunSettings(primary_model='gemini-test'), 'key')
        self.console = Mock()

    def test_run_declined_layout_stops_without_credentials(self):
        self.mocks['ensure_project_layout'].return_value = False
        args = cli.build_parser().parse_args(['run', '.'])
        self.assertEqual(cli.run_command(args, Path('.'), False, self.console), 0)
        self.mocks['_run_inputs'].assert_not_called()
        self.mocks['run_project'].assert_not_called()

    def test_run_outcomes_and_option_forwarding(self):
        for summary, code in [(None, 0), (SimpleNamespace(failed_pdfs=0), 0), (SimpleNamespace(failed_pdfs=2), 1)]:
            with self.subTest(summary=summary):
                self.mocks['run_project'].return_value = summary
                args = cli.build_parser().parse_args(['run', '.', '--yes', '--force', '--skip-report', '--extractor', 'Lab'])
                self.assertEqual(cli.run_command(args, Path('.'), False, self.console), code)
                kwargs = self.mocks['run_project'].call_args.kwargs
                self.assertEqual(kwargs['project_dir'], Path.cwd())
                self.assertTrue(kwargs['force'])
                self.assertTrue(kwargs['skip_report'])
                self.assertTrue(kwargs['assume_yes'])
                self.assertFalse(kwargs['interactive'])
                self.assertEqual(self.mocks['_run_inputs'].call_args.args[0].extractor, 'Lab')
                self.assertEqual(kwargs['api_key'], 'key')
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
                    args = cli.build_parser().parse_args(['retry', '.', '--source', 'a.pdf', '--source', 'nested/b.pdf', '--yes', '--skip-report', *(['--skip-preflight'] if skip else [])])
                    self.assertEqual(cli.retry_command(args, self.console), code)
                    self.assertEqual(self.mocks['run_preflight'].called, not skip)
                    self.assertFalse(self.mocks['_run_inputs'].call_args.args[2])
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
        summary = SimpleNamespace(report_path=Path('/project/report.html'), successful_pdfs=2, needs_review_pdfs=1, failed_pdfs=1, extracted_effects=3, usable_zcurve_inputs=2, input_tokens=4, output_tokens=5, total_tokens=9)
        console = Mock()
        cli.print_summary(summary, console)
        self.assertIn(('Total tokens', 9), console.table.call_args.args[2])
        self.assertIn(('Needs review articles', 1), console.table.call_args.args[2])
        summary.report_path = None
        with patch.object(cli, 'format_run_result', return_value='Z-Curve Summary: values'):
            cli.print_summary(summary, console, Path('.'))
        self.assertIn(('Report', 'not rendered'), console.table.call_args.args[2])
        console.print.assert_called_with('values')
