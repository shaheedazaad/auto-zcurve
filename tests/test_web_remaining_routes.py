import io
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from fastapi.testclient import TestClient
from auto_zcurve import web as w
from auto_zcurve.config import AppSettings, RunSettings, save_run_settings
from auto_zcurve.projects import create_project


class RemainingRouteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')})
        env.start()
        self.addCleanup(env.stop)
        self.project = create_project('Test', root=root / 'projects')
        self.app = w.create_app(token='token', projects_root=root / 'projects')
        self.runtime = self.app.state.runtime
        self.base = '/token/projects/' + self.project.project_id
        self.client = TestClient(self.app, base_url='http://127.0.0.1')
        self.addCleanup(self.client.close)

    def test_invalid_credentials_and_missing_saved_key(self):
        for suffix in ['', '/load', '/delete']:
            self.assertEqual(self.client.post('/token/credentials' + suffix, data={'provider': 'bad', 'api_key': 'key'}).status_code, 400)
        self.assertEqual(self.client.post('/token/credentials', data={'api_key': ' '}).status_code, 400)
        with patch.object(w, 'load_saved_api_key', return_value=None):
            self.assertEqual(self.client.post('/token/credentials/load').status_code, 404)

    def test_missing_resource_routes_and_cancel_without_job(self):
        missing = '/token/projects/' + 'f' * 16
        for suffix in ['/events', '/report', '/zcurve-plot', '/results.zip']:
            self.assertEqual(self.client.get(missing + suffix).status_code, 404)
        self.assertEqual(self.client.post(missing + '/open-folder').status_code, 404)
        for suffix in ['/report', '/zcurve-plot']:
            self.assertEqual(self.client.get(self.base + suffix).status_code, 404)
        self.assertEqual(self.client.post(self.base + '/cancel').status_code, 409)
        self.assertEqual(self.client.post(missing + '/regenerate-report').status_code, 409)

    def test_endpoint_run_and_retry_require_configuration_and_unlock(self):
        for action in ['run', 'retry']:
            for configured, base_url, message in [(True, 'http://localhost/v1', 'could not be unlocked'), (False, '', 'base URL')]:
                with patch.object(self.runtime, '_load_key_from_store', return_value=None), patch.object(w, 'saved_api_key_configured', return_value=configured), patch.object(w, 'load_app_settings', return_value=AppSettings(openai_base_url=base_url)):
                    response = self.client.post(self.base + '/' + action, data={'provider': 'openai_compatible', 'model': 'local'})
                self.assertEqual(response.status_code, 409)
                self.assertIn(message, response.text)
        with patch.object(self.runtime, '_load_key_from_store', return_value=None):
            self.assertEqual(self.client.post(self.base + '/retry').status_code, 409)

    def test_openrouter_run_retry_validation_and_saved_defaults(self):
        save_run_settings(self.project.path, RunSettings(primary_model='vendor/old', provider='openrouter', parallel_requests=4, request_delay_sec=5))
        with patch.object(self.runtime, '_load_key_from_store', return_value='key'), patch.object(w, 'validate_model_option') as validate, patch.object(self.runtime, 'start_job', return_value=NS(status='queued')) as start:
            for action in ['run', 'retry']:
                response = self.client.post(self.base + '/' + action, data={'provider': 'openrouter', 'model': 'vendor/new'})
                self.assertEqual(response.status_code, 202, response.text)
                settings = start.call_args.args[2]
                self.assertEqual((settings.parallel_requests, settings.request_delay_sec), (4, 5))
                validate.assert_called_with('openrouter', 'vendor/new', 'key')
            response = self.client.post(self.base + '/retry', data={'provider': 'gemini', 'model': 'gemini-test'})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(start.call_args.args[2].provider, 'gemini')
        with patch.object(self.runtime, 'start_job', return_value=NS(status='queued')) as start:
            self.assertEqual(self.client.post(self.base + '/regenerate-report').status_code, 202)
            self.assertEqual(start.call_args.args[2].primary_model, 'vendor/old')

    def test_instruction_validation_schema_validation_and_job_reset(self):
        self.assertEqual(self.client.post(self.base + '/instructions', data={'instructions': ' '}).status_code, 400)
        self.assertEqual(self.client.post(self.base + '/schema', data={'schema_text': 'effects: []'}).status_code, 400)
        for suffix, payload in [('/instructions', {'instructions': 'new instructions'}), ('/schema', {'schema_text': 'effects: {claim: {type: string}}'})]:
            (self.project.path / 'output/report.html').write_text('report')
            self.runtime.jobs[self.project.project_id] = w.Job(self.project.project_id, 'run')
            self.runtime.jobs[self.project.project_id].status = 'complete'
            response = self.client.post(self.base + suffix, data={**payload, 'confirm_reset': 'yes'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['reset'])
            self.assertNotIn(self.project.project_id, self.runtime.jobs)

    def test_response_deletion_and_plot_download(self):
        from auto_zcurve.artifacts import save_extractions
        save_extractions(self.project.path, [{'source_name': 'a.pdf', 'status': 'error'}])
        response = self.client.post(self.base + '/responses/delete', data={'source_name': 'a.pdf'})
        self.assertEqual(response.json(), {'deleted': True, 'source_name': 'a.pdf'})
        plot = self.project.path / 'output/zcurve_plot.png'
        plot.write_bytes(b'png fixture')
        response = self.client.get(self.base + '/zcurve-plot')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'png fixture')
        self.assertEqual(response.headers['content-type'], 'image/png')

    def test_minimal_zip_and_folder_open_failure(self):
        (self.project.path / 'extraction_schema.yml').unlink()
        shutil.rmtree(self.project.path / 'output')
        response = self.client.get(self.base + '/results.zip')
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.namelist(), [])
        with patch.object(w.platform, 'system', return_value='Windows'), patch.object(w.os, 'startfile', side_effect=OSError('not available'), create=True):
            self.assertEqual(self.client.post(self.base + '/open-folder').status_code, 503)

    def test_fallback_catalog_and_successful_model_validation(self):
        option = NS(name='vendor/model', parallel_requests=3, request_delay_sec=2)
        with patch.object(w, 'fallback_models', return_value=[]) as fallback, patch.object(w, 'list_live_models') as live:
            response = self.client.get('/token/api/models/gemini')
            self.assertEqual(response.json(), [])
            fallback.assert_called_once_with('gemini')
            live.assert_not_called()
        with patch.object(self.runtime, '_load_key_from_store', return_value='key'), patch.object(w, 'validate_model_option', return_value=option):
            response = self.client.post('/token/api/models/openrouter/validate', data={'model': 'vendor/model'})
            self.assertEqual(response.json(), {'valid': True, 'id': 'vendor/model', 'parallel_requests': 3, 'request_delay_sec': 2})

    def test_edits_without_results_do_not_reset_and_invalid_host_ports_are_rejected(self):
        for suffix, payload in [('/instructions', {'instructions': 'new instructions'}), ('/schema', {'schema_text': 'effects: {claim: {type: string}}'})]:
            response = self.client.post(self.base + suffix, data=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()['reset'])
        for host in ['localhost:invalid', 'localhost:80:90', '127.0.0.1:']:
            self.assertEqual(self.client.get('/token/', headers={'host': host}).status_code, 400)
