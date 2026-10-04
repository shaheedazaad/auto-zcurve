import builtins
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from auto_zcurve import web as w
from auto_zcurve.config import RunSettings
from auto_zcurve.projects import ManagedProject, ProjectError
from auto_zcurve.runner import RunSummary, RunCancelled


class WebRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = w.WebRuntime('token')
        self.project = ManagedProject('a' * 16, 'Test', Path('/project'), '')
        self.settings = RunSettings(primary_model='model')

    def test_console_events_redact_keys_and_snapshot_is_independent(self):
        job = w.Job('project', 'run')
        console = w.WebConsole(job, 'secret')
        console.print('value', 'secret')
        console.title('secret')
        console.title('title', 'secret')
        for method in ['info', 'warn', 'error', 'success']:
            getattr(console, method)('secret')
        console.table('table', ['value'], [['secret']])
        with console.progress(2, 'Extracting') as progress:
            progress.advance()
            progress.advance('done')
        snapshot = job.snapshot()
        self.assertNotIn('secret', json.dumps(snapshot))
        self.assertIn('[redacted]', json.dumps(snapshot))
        snapshot['events'].clear()
        self.assertTrue(job.events)
        self.assertEqual(job.events[-1]['completed'], 2)

    def test_legacy_key_properties_and_store_loading(self):
        self.runtime.session_api_key = 'key'
        self.assertEqual(self.runtime.session_api_key, 'key')
        self.runtime.key_warning = 'warning'
        self.assertEqual(self.runtime.key_warning, 'warning')
        self.runtime.key_warning = None
        self.assertIsNone(self.runtime.key_warning)
        with patch.object(w, 'load_saved_api_key') as load:
            self.assertEqual(self.runtime._load_key_from_store('gemini'), 'key')
            load.assert_not_called()
        self.runtime.session_api_key = None
        with patch.object(w, 'saved_api_key_configured', return_value=False), patch.object(w, 'load_saved_api_key') as load:
            self.assertIsNone(self.runtime._load_key_from_store('gemini'))
            load.assert_not_called()
        for key in [None, 'saved']:
            with patch.object(w, 'saved_api_key_configured', return_value=True), patch.object(w, 'load_saved_api_key', return_value=key):
                self.assertEqual(self.runtime._load_key_from_store('gemini'), key)
        self.assertEqual(self.runtime.secrets(), ['saved'])

    def test_update_check_new_old_invalid_and_network_failure(self):
        for tag, expected in [('v99.0.0', '99.0.0'), ('v0.0.1', None), ('invalid', None)]:
            runtime = w.WebRuntime('token')
            with patch.object(w.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps({'tag_name': tag}).encode())):
                runtime.check_for_update()
            self.assertEqual(runtime.update_version, expected)
        with patch.object(w.urllib.request, 'urlopen', side_effect=OSError('offline')):
            self.runtime.check_for_update()
        self.assertIsNone(self.runtime.update_version)

    def test_start_job_rejects_active_and_replaces_terminal_jobs(self):
        with patch.object(w.threading, 'Thread') as thread:
            job = self.runtime.start_job(self.project, 'run', self.settings)
            thread.return_value.start.assert_called_once()
            for status in ['queued', 'running']:
                job.status = status
                with self.assertRaisesRegex(ProjectError, 'already running'):
                    self.runtime.start_job(self.project, 'run', self.settings)
            job.status = 'complete'
            replacement = self.runtime.start_job(self.project, 'retry', self.settings)
            self.assertIsNot(replacement, job)

    def test_jobs_dispatch_complete_and_cancel(self):
        self.runtime.session_api_key = 'secret'
        summary = RunSummary(Path('/project/output/report.html'), 1, 0, 2, 1, 3, 4, 7)
        for action, function in [('run', 'run_project'), ('retry', 'retry_project'), ('report', 'regenerate_report')]:
            for result in [summary, None]:
                job = w.Job(self.project.project_id, action)
                with patch.object(w, 'run_preflight'), patch.object(w, function, return_value=result) as operation:
                    self.runtime._run_job(job, self.project, action, self.settings)
                operation.assert_called_once()
                self.assertEqual(job.status, 'complete')
                self.assertEqual(job.summary['report_path'] if job.summary else None, str(summary.report_path) if result else None)
            for cancellation in [True, False]:
                job = w.Job(self.project.project_id, action)
                def cancel(**kwargs):
                    if cancellation:
                        job.cancel_event.set()
                        return None
                    raise RunCancelled('cancelled')
                with patch.object(w, 'run_preflight'), patch.object(w, function, side_effect=cancel):
                    self.runtime._run_job(job, self.project, action, self.settings)
                self.assertEqual(job.status, 'cancelled')

    def test_jobs_fail_with_friendly_redacted_errors(self):
        job = w.Job(self.project.project_id, 'run')
        self.runtime._run_job(job, self.project, 'run', self.settings)
        self.assertEqual(job.status, 'failed')
        self.assertIn('API key missing', job.error['title'])
        self.runtime.session_api_key = 'secret'
        with patch.object(w, 'run_preflight', side_effect=RuntimeError('failure secret')):
            self.runtime._run_job(job, self.project, 'run', self.settings)
        self.assertNotIn('secret', json.dumps(job.snapshot()))
        self.assertIn('[redacted]', job.error['technical_detail'])


class WebLaunchTests(unittest.TestCase):
    def test_launch_binds_localhost_and_optional_browser(self):
        app = NS(state=NS(runtime=Mock()))
        for port, browser in [(8123, False), (None, True)]:
            with patch.object(w, 'create_app', return_value=app), patch.object(w.secrets, 'token_urlsafe', return_value='token'), patch.object(w.socket, 'socket') as socket, patch.object(w.threading, 'Thread'), patch.object(w.threading, 'Timer') as timer, patch('uvicorn.run') as run, patch('builtins.print'):
                socket.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 9123)
                self.assertEqual(w.launch_web(open_browser=browser, port=port), 0)
                run.assert_called_once_with(app, host='127.0.0.1', port=port or 9123, log_level='warning', access_log=False)
                self.assertEqual(timer.called, browser)
                if browser:
                    timer.assert_called_once_with(0.5, w.webbrowser.open, args=('http://127.0.0.1:9123/token/',))

    def test_missing_server_dependency(self):
        importing = builtins.__import__
        def import_module(name, *args, **kwargs):
            if name == 'uvicorn':
                raise ImportError('missing')
            return importing(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=import_module), self.assertRaisesRegex(RuntimeError, 'dependencies are not installed'):
            w.launch_web()
