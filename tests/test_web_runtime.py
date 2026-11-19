import builtins
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from auto_zcurve import credentials
from auto_zcurve import web as w
from auto_zcurve.extractors import Extractor
from tests.test_key_vault import FakeKeyring
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
        console = w.WebConsole(job, ['secret'])
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

    def test_keys_are_read_only_for_extractors_that_have_one(self):
        keyring = FakeKeyring({('auto-zcurve', 'extractor_keys'): json.dumps({'keys': {'saved': 'saved-key'}})})
        unsaved = Extractor(id='unsaved', name='A', provider='gemini', model='m')
        saved = Extractor(id='saved', name='B', provider='gemini', model='m', key_saved=True)
        migrated = Extractor(id='old', name='C', provider='gemini', model='m', legacy_credential='gemini_api_key')
        with patch.object(credentials, '_keyring_module', return_value=keyring), patch.object(w, 'legacy_claims', return_value={}):
            self.runtime = w.WebRuntime('token')
            self.assertFalse(self.runtime.has_key(unsaved))
            self.assertIsNone(self.runtime.key_for(unsaved))
            self.assertEqual(keyring.reads, 0)
            self.assertTrue(self.runtime.has_key(saved))
            self.assertTrue(self.runtime.has_key(migrated))
            self.assertEqual(self.runtime.key_for(saved), 'saved-key')
            self.assertEqual(self.runtime.key_for(saved), 'saved-key')
            self.assertIsNone(self.runtime.key_for(migrated))
        self.assertEqual(keyring.reads, 1)
        self.assertEqual(self.runtime.secrets(), ['saved-key'])

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
        summary = RunSummary(Path('/project/output/report.html'), 1, 0, 2, 1, 3, 4, 7)
        for action, function in [('run', 'run_project'), ('retry', 'retry_project'), ('report', 'regenerate_report')]:
            for result in [summary, None]:
                job = w.Job(self.project.project_id, action)
                with patch.object(w, 'run_preflight'), patch.object(w, function, return_value=result) as operation:
                    self.runtime._run_job(job, self.project, action, self.settings, 'secret')
                operation.assert_called_once()
                if action != 'report':
                    self.assertEqual(operation.call_args.kwargs['api_key'], 'secret')
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
                    self.runtime._run_job(job, self.project, action, self.settings, None)
                self.assertEqual(job.status, 'cancelled')

    def test_jobs_fail_with_friendly_redacted_errors(self):
        job = w.Job(self.project.project_id, 'run')
        with patch.object(credentials, '_keyring_module', return_value=FakeKeyring()):
            self.runtime.vault.set('x', 'secret')
        with patch.object(w, 'run_preflight', side_effect=RuntimeError('failure secret')):
            self.runtime._run_job(job, self.project, 'run', self.settings, 'secret')
        self.assertEqual(job.status, 'failed')
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
