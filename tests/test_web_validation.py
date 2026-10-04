import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from auto_zcurve import web as w
from auto_zcurve.models import ModelOption
from auto_zcurve.projects import create_project


class WebValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        env = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config')})
        env.start()
        self.addCleanup(env.stop)
        self.app = w.create_app(token='token', projects_root=root / 'projects')
        self.project = create_project('Test', root=root / 'projects')
        self.base = '/token/projects/' + self.project.project_id
        self.client = TestClient(self.app, base_url='http://127.0.0.1')
        self.addCleanup(self.client.close)

    def test_security_limits_and_origins(self):
        for headers, status in [({'Host': 'evil.test'}, 400), ({'Content-Length': str(w.MAX_UPLOAD_REQUEST_BYTES + 1)}, 413), ({'Origin': 'https://evil.test'}, 403), ({'Sec-Fetch-Site': 'cross-site'}, 403)]:
            response = self.client.post('/token/projects', data={'name': 'Test'}, headers=headers)
            self.assertEqual(response.status_code, status, response.text)
        response = self.client.post('/token/projects', data={'name': 'Valid'}, headers={'Origin': 'https://127.0.0.1/'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)

    def test_missing_projects_return_404_across_routes(self):
        base = '/token/projects/' + 'f' * 16
        cases = [('', 'get', {}), ('/reset', 'post', {}), ('/delete', 'post', {}), ('/rename', 'post', {'name': 'Name'}), ('/responses/delete', 'post', {'source_name': 'a.pdf'}), ('/instructions', 'post', {'instructions': 'text'}), ('/schema', 'post', {'schema_text': 'text'})]
        for suffix, method, data in cases:
            with self.subTest(suffix=suffix):
                response = getattr(self.client, method)(base + suffix, **({'data': data} if method == 'post' else {}))
                self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.client.get('/token/api/projects/' + 'f' * 16).status_code, 404)
        self.assertEqual(self.client.post(base + '/uploads', files={'files': ('a.pdf', b'%PDF')}).status_code, 404)

    def test_project_mutation_validation_and_active_job_guards(self):
        self.assertEqual(self.client.post('/token/projects', data={'name': ' '}).status_code, 400)
        self.assertEqual(self.client.post(self.base + '/rename', data={'name': ' '}).status_code, 400)
        for source in ['../a.pdf', 'file.txt', ' ']:
            self.assertEqual(self.client.post(self.base + '/responses/delete', data={'source_name': source}).status_code, 400)
        self.assertEqual(self.client.post(self.base + '/responses/delete', data={'source_name': 'a.pdf'}).status_code, 404)
        job = w.Job(self.project.project_id, 'run')
        self.app.state.runtime.jobs[self.project.project_id] = job
        for suffix, data in [('/reset', {}), ('/delete', {}), ('/responses/delete', {'source_name': 'a.pdf'}), ('/instructions', {'instructions': 'text'}), ('/schema', {'schema_text': 'text'})]:
            with self.subTest(suffix=suffix):
                self.assertEqual(self.client.post(self.base + suffix, data=data).status_code, 409)

    def test_settings_and_catalog_failures(self):
        self.assertEqual(self.client.post('/token/settings/providers/unknown').status_code, 400)
        self.assertEqual(self.client.post('/token/settings/providers/gemini', data={'service_tier': 'bad'}).status_code, 400)
        self.assertEqual(self.client.post('/token/settings/endpoint', data={'openai_base_url': 'bad'}).status_code, 400)
        self.assertEqual(self.client.get('/token/api/models/unknown').status_code, 503)
        self.app.state.runtime.session_api_key = 'key'
        with patch.object(w, 'list_live_models', side_effect=RuntimeError('offline')):
            self.assertEqual(self.client.get('/token/api/models/gemini').status_code, 503)
        with patch.object(w, 'list_live_models', return_value=[ModelOption('a', 'A')]), patch.object(w, 'resolve_input_mode', side_effect=ValueError('unsupported')):
            self.assertEqual(self.client.get('/token/api/models/gemini').json(), [])
        with patch.object(self.app.state.runtime, '_load_key_from_store', return_value=None):
            self.assertEqual(self.client.post('/token/api/models/openrouter/validate', data={'model': 'a'}).status_code, 400)
        with patch.object(self.app.state.runtime, '_load_key_from_store', return_value='key'), patch.object(w, 'validate_model_option', side_effect=ValueError('bad model')):
            self.assertEqual(self.client.post('/token/api/models/openrouter/validate', data={'model': 'a'}).status_code, 400)

    def test_upload_count_and_size_limits(self):
        response = self.client.post(self.base + '/uploads', files=[('files', ('a.pdf', b'%PDF'))] * 101)
        self.assertEqual(response.status_code, 413)
        with patch.object(w, 'save_upload', side_effect=w.UploadTooLarge('too large')):
            response = self.client.post(self.base + '/uploads', files={'files': ('a.pdf', b'%PDF')})
        self.assertEqual(response.status_code, 413)

    def test_ipv6_loopback_host_is_allowed(self):
        response = self.client.get('/token/', headers={'Host': '[::1]:8123'})
        self.assertEqual(response.status_code, 200, response.text)

    def test_malformed_or_nonloopback_bracketed_hosts_are_rejected(self):
        for host in ['[::2]:8123', '[::1]evil.test', '[::1]:8123evil']:
            with self.subTest(host=host):
                response = self.client.get('/token/', headers={'Host': host})
                self.assertEqual(response.status_code, 400, response.text)
