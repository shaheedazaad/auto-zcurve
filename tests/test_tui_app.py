import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from textual.app import App
from textual.widgets import Button, Input, Select

from auto_zcurve import tui
from auto_zcurve.models import ModelOption


class TuiAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.project = Path(tmp).resolve()
        (self.project / 'sources').mkdir()
        (self.project / 'sources/a.pdf').write_bytes(b'%PDF')
        (self.project / 'extraction_schema.yml').write_text('effects: {claim: {type: string}}')
        self.stack.enter_context(patch.object(tui, 'request_terminal_resize'))
        self.stack.enter_context(patch.object(tui, 'load_last_project_dir', return_value=self.project))
        self.stack.enter_context(patch.object(tui, 'saved_api_key_configured', return_value=False))
        self.stack.enter_context(patch.object(tui, 'fallback_models', return_value=[ModelOption('gemini-test', 'Test')]))
        self.readiness = self.stack.enter_context(patch.object(tui, 'check_project_readiness', return_value=NS(ready=True, next_action='Ready')))
        self.failures = self.stack.enter_context(patch.object(tui, 'failed_pdf_rows', return_value=[]))
        self.saved_project = self.stack.enter_context(patch.object(tui, 'save_last_project_dir'))
        captured = []
        with patch.object(App, 'run', autospec=True, side_effect=lambda app: captured.append(app)):
            self.assertEqual(tui.run_tui(), 0)
        self.app = captured[0]

    async def test_startup_readiness_progress_and_report(self):
        async with self.app.run_test(size=(140, 55)) as pilot:
            self.assertFalse(self.app.query_one('#run', Button).disabled)
            self.assertTrue(self.app.query_one('#retry', Button).disabled)
            self.assertTrue(self.app.query_one('#open_report', Button).disabled)
            self.failures.return_value = [('a.pdf', 'failed')]
            self.app.refresh_readiness()
            self.assertFalse(self.app.query_one('#retry', Button).disabled)
            self.failures.side_effect = ValueError('corrupt')
            self.app.refresh_readiness()
            self.assertTrue(self.app.query_one('#retry', Button).disabled)
            self.app.set_busy(True)
            self.app.refresh_readiness()
            self.assertTrue(self.app.query_one('#run', Button).disabled)
            self.app.set_busy(False)
            self.app._reset_progress(0, 'Starting')
            self.app._advance_progress('ok: a.pdf', 1, 1)
            self.assertEqual(self.app.progress_widget.progress, 1)
            report = self.project / 'output/report.html'
            report.parent.mkdir()
            report.touch()
            self.app.refresh_readiness()
            self.assertFalse(self.app.query_one('#open_report', Button).disabled)
            with patch.object(tui, 'open_report_path') as open_report:
                await pilot.click('#open_report')
                open_report.assert_called_once_with(report)
            with patch.object(tui, 'open_report_path', side_effect=OSError('unavailable')):
                self.app.open_report()
            self.app.last_report_path = self.project.parent / 'outside.html'
            self.assertEqual(self.app._current_report_path(), report)
            self.app.last_report_path = report
            self.assertEqual(self.app._current_report_path(), report)
            with patch.object(self.app, 'notify', side_effect=RuntimeError()):
                self.app.feedback('message')
            self.app.refresh_article_summary()

    async def test_key_actions_and_directory_selection(self):
        async with self.app.run_test(size=(140, 55)) as pilot:
            key = self.app.query_one('#api_key', Input)
            key.value = 'secret'
            await pilot.pause()
            with patch.object(tui, 'save_api_key', return_value='test store') as save:
                await pilot.click('#save_key')
                save.assert_called_once_with('secret')
            with patch.object(tui, 'save_api_key', side_effect=ValueError('empty')):
                self.app.save_key()
            with patch.object(tui, 'load_saved_api_key', return_value=None):
                await pilot.click('#load_key')
            with patch.object(tui, 'load_saved_api_key', return_value='loaded'):
                self.app.load_key()
                self.assertEqual(key.value, 'loaded')
            for deleted in [True, False]:
                with patch.object(tui, 'delete_saved_api_key', return_value=deleted):
                    self.app.delete_key()
                    self.assertEqual(key.value, '')
            self.app.set_project_directory(None)
            self.app.set_project_directory(self.project)
            self.saved_project.assert_called_with(self.project)
            await pilot.click('#browse_project')
            await pilot.pause()
            self.assertEqual(type(self.app.screen).__name__, 'DirectoryPicker')
            await pilot.press('escape')
            await pilot.pause()
            self.assertEqual(type(self.app.screen).__name__, 'Screen')

    async def test_run_validation_and_worker_dispatch(self):
        async with self.app.run_test(size=(140, 55)) as pilot:
            self.app.query_one('#api_key', Input).value = 'key'
            for field in ['parallel_requests', 'timeout_sec', 'max_upload_mb']:
                widget = self.app.query_one('#' + field, Input)
                old = widget.value
                widget.value = 'bad'
                with patch.object(self.app, '_begin_run') as begin:
                    self.app.start_run(False)
                    begin.assert_not_called()
                widget.value = old
            self.app.query_one('#api_key', Input).value = ''
            with patch.object(self.app, '_begin_run') as begin:
                self.app.start_run(False)
                begin.assert_not_called()
            self.app.query_one('#api_key', Input).value = 'key'
            self.readiness.return_value = NS(ready=False, next_action='Missing dependencies')
            with patch.object(self.app, '_begin_run') as begin:
                self.app.start_run(False)
                begin.assert_not_called()
            self.readiness.return_value = NS(ready=True, next_action='Ready')
            with patch.object(tui, 'Thread') as thread:
                self.app.start_run(False)
                thread.return_value.start.assert_called_once()
                self.assertFalse(thread.call_args.kwargs['kwargs']['retry'])
                self.assertTrue(self.app.busy)
            self.app.set_busy(False)
            with patch.object(tui, 'Thread') as thread:
                self.app.start_run(True)
                self.assertTrue(thread.call_args.kwargs['kwargs']['retry'])

    async def test_worker_results_failures_and_console_output(self):
        import asyncio
        from auto_zcurve.runner import RunSummary
        async with self.app.run_test(size=(140, 55)) as pilot:
            kwargs = dict(project_dir=self.project, api_key='key', model='gemini-test', retry=False, parallel_requests=2, timeout_sec=30, max_upload_mb=10)
            summary = RunSummary(self.project / 'output/report.html', 1, 1, 2, 1, 4, 5, 9)
            def run(**arguments):
                console = arguments['console']
                console.print('plain', '[text]')
                console.title('title', 'subtitle')
                console.title('title')
                console.info('information')
                console.warn('warning')
                console.error('error')
                console.success('success')
                console.table('Three columns', ['a', 'b', 'c'], [[1, 2, 3]])
                with console.progress(3, 'Extracting') as progress:
                    progress.advance('ok: a.pdf')
                    progress.advance('error: b.pdf')
                    progress.advance()
                return summary
            with patch.object(tui, 'run_preflight'), patch.object(tui, 'load_run_settings', return_value=None), patch.object(tui, 'run_project', side_effect=run), patch.object(tui, 'read_zcurve_summary', return_value='ERR 0.5'), patch.object(tui, 'failed_pdf_rows', return_value=[('b.pdf', 'failed')]):
                await asyncio.to_thread(self.app._run_worker, **kwargs)
            self.assertEqual(self.app.last_report_path, summary.report_path)
            self.assertFalse(self.app.busy)
            self.assertTrue(self.app.log_widget.lines)
            for outcome in [None, RunSummary(None, 1, 0, 1, 1, 0, 0, 0), RuntimeError('failed')]:
                with patch.object(tui, 'run_preflight'), patch.object(tui, 'load_run_settings', return_value=None), patch.object(tui, 'retry_project', **({'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome})) as retry, patch.object(tui, 'read_zcurve_summary', return_value=None):
                    await asyncio.to_thread(self.app._run_worker, **dict(kwargs, retry=True))
                    retry.assert_called_once()
                    self.assertFalse(self.app.busy)

    async def test_schema_confirmation_cancel_accept_and_copy_failure(self):
        async with self.app.run_test(size=(140, 55)) as pilot:
            self.app.query_one('#api_key', Input).value = 'key'
            schema = self.project / 'extraction_schema.yml'
            schema.unlink()
            await pilot.pause()
            self.app.start_run(False)
            await pilot.pause()
            self.assertEqual(type(self.app.screen).__name__, 'DefaultSchemaModal')
            await pilot.click('#cancel_schema_notice')
            await pilot.pause()
            self.assertFalse(schema.exists())
            self.app.start_run(False)
            await pilot.pause()
            with patch.object(self.app, '_begin_run') as begin:
                await pilot.click('#use_default_schema')
                await pilot.pause()
                begin.assert_called_once()
            self.assertEqual(schema.read_bytes(), tui.DEFAULT_SCHEMA.read_bytes())
            kwargs = dict(project_dir=self.project, api_key='key', model='gemini-test', retry=False, parallel_requests=1, timeout_sec=30, max_upload_mb=10)
            with patch.object(tui.shutil, 'copyfile', side_effect=OSError('read only')), patch.object(self.app, '_begin_run') as begin:
                self.app._handle_default_schema_choice(True, **kwargs)
                begin.assert_not_called()
            self.app._handle_default_schema_choice(None, **kwargs)

    async def test_directory_picker_navigation_and_controls(self):
        from textual.widgets import DirectoryTree
        async with self.app.run_test(size=(140, 55)) as pilot:
            self.app.open_project_picker()
            await pilot.pause()
            picker = self.app.screen
            picker.on_button_pressed(NS(button=NS(id='unknown')))
            self.assertIs(self.app.screen, picker)
            picker.on_directory_tree_directory_selected(NS(path=self.project / 'sources'))
            self.assertEqual(picker.selected_path, self.project / 'sources')
            picker.on_directory_tree_file_selected(NS(path=self.project / 'sources/a.pdf'))
            self.assertEqual(picker.selected_path, self.project / 'sources')
            picker.on_directory_tree_file_selected(NS(path=self.project / 'absent/file.pdf'))
            picker.on_tree_node_selected(NS(node=NS(data=NS(path=self.project))))
            self.assertEqual(picker.selected_path, self.project)
            picker.on_tree_node_selected(NS(node=NS(data=None)))
            picker.on_tree_node_selected(NS(node=NS(data=self.project / 'sources/a.pdf')))
            tree = picker.query_one('#directory_tree', DirectoryTree)
            self.assertEqual(list(tree.filter_paths([Path('.hidden'), Path('visible')])), [Path('visible')])
            picker.selected_path = self.project / 'sources'
            await pilot.click('#parent_directory')
            await pilot.pause()
            self.assertEqual(picker.selected_path, self.project)
            picker.selected_path = self.project / 'sources'
            with patch.object(tree, 'reload', side_effect=RuntimeError('refresh failed')):
                picker.go_up()
            picker.selected_path = Path('/')
            picker.go_up()
            self.assertEqual(picker.selected_path, Path('/'))
            picker.selected_path = self.project
            await pilot.click('#use_directory')
            await pilot.pause()
            self.saved_project.assert_called_with(self.project)
            self.app.query_one('#project_dir', Input).value = str(self.project / 'absent')
            self.app.open_project_picker()
            await pilot.pause()
            self.assertEqual(self.app.screen.selected_path, Path.cwd().resolve())
            await pilot.click('#cancel_directory')
            await pilot.pause()

    async def test_remaining_dispatch_and_empty_model(self):
        async with self.app.run_test(size=(140, 55)) as pilot:
            self.app.query_one('#api_key', Input).value = 'key'
            await pilot.pause()
            for button, method, expected in [('run', 'start_run', False), ('retry', 'start_run', True), ('delete_key', 'delete_key', None)]:
                with patch.object(self.app, method) as action:
                    self.app.on_button_pressed(NS(button=NS(id=button)))
                    if expected is None:
                        action.assert_called_once_with()
                    else:
                        action.assert_called_once_with(retry=expected)
            self.app.on_button_pressed(NS(button=NS(id='unknown')))
            self.app.on_select_changed(NS(select=NS(id='other')))
            self.app.on_input_changed(NS(input=NS(id='other')))
            self.assertIn('Unlock', self.app._key_status(True))
            selector = self.app.query_one('#model', Select)
            selector.value = Select.NULL
            self.app.refresh_readiness()
            self.assertEqual(self.readiness.call_args.kwargs['model'], '')
            with patch.object(self.app, '_begin_run') as begin:
                self.app.start_run(False)
                begin.assert_not_called()
            selector.value = 'gemini-test'
            self.app.query_one('#project_dir', Input).value = str(self.project / 'empty')
            self.app.refresh_article_summary()
            with patch.object(tui, 'Thread'):
                self.app._begin_run(project_dir=self.project / 'empty', api_key='key', model='gemini-test', retry=False, parallel_requests=1, timeout_sec=30, max_upload_mb=10)

    async def test_schema_escape_and_ignored_button(self):
        async with self.app.run_test(size=(140, 55)) as pilot:
            self.app.query_one('#api_key', Input).value = 'key'
            (self.project / 'extraction_schema.yml').unlink()
            self.app.start_run(False)
            await pilot.pause()
            self.app.screen.on_button_pressed(NS(button=NS(id='other')))
            await pilot.press('escape')
            await pilot.pause()
            self.assertEqual(type(self.app.screen).__name__, 'Screen')
