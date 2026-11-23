import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from auto_zcurve import credentials
from auto_zcurve import web as w
from tests.test_key_vault import FakeKeyring
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

    def test_extractor_route_failures(self):
        with patch.object(credentials, '_keyring_module', return_value=FakeKeyring()):
            self.assertEqual(self.client.post('/token/extractors', data={'name': 'X', 'provider': 'bad', 'model': 'm'}).status_code, 400)
            created = self.client.post('/token/extractors', data={'name': 'Local', 'provider': 'openai_compatible', 'model': 'm', 'base_url': 'http://h/v1'}).json()
            self.assertIsNone(created['warning'])
            local = created['extractor']['id']
            missing = 'f' * 8
            for path, status in [
                (f'/token/extractors/{missing}', 404),
                (f'/token/extractors/{missing}/duplicate', 404),
                (f'/token/extractors/{missing}/delete', 404),
                (f'/token/extractors/{missing}/key/delete', 404),
            ]:
                with self.subTest(path=path):
                    self.assertEqual(self.client.post(path, data={'name': 'Y', 'model': 'm'}).status_code, status)
            self.assertEqual(self.client.post(f'/token/extractors/{missing}/key', data={'api_key': 'k'}).status_code, 404)
            self.assertEqual(self.client.post(f'/token/extractors/{local}/key', data={'api_key': ' '}).status_code, 400)
            self.assertEqual(self.client.post(f'/token/extractors/{local}', data={'name': 'Local', 'model': 'n', 'base_url': 'ftp://bad'}).status_code, 400)
            self.assertEqual(self.client.get(f'/token/api/extractors/{missing}/models').status_code, 404)
            self.assertEqual(self.client.get(f'/token/api/extractors/{local}/models').json(), [])
            gemini = self.client.post('/token/extractors', data={'name': 'G', 'provider': 'gemini', 'model': 'm'}).json()['extractor']['id']
            self.assertEqual(self.client.get(f'/token/api/extractors/{gemini}/models').json(), [])
            self.client.post(f'/token/extractors/{gemini}/key', data={'api_key': 'k'})
            with patch.object(w, 'list_live_models', side_effect=RuntimeError('offline')):
                self.assertEqual(self.client.get(f'/token/api/extractors/{gemini}/models').status_code, 503)
            with patch.object(w, 'list_live_models', return_value=[ModelOption('a', 'A')]):
                self.assertEqual(self.client.get(f'/token/api/extractors/{gemini}/models').json(), [{'id': 'a', 'name': 'A'}])
            self.assertEqual(self.client.post(self.base + '/settings', data={'extractor_id': missing}).status_code, 400)
            self.assertEqual(self.client.post('/token/projects/' + 'f' * 16 + '/settings', data={'extractor_id': local}).status_code, 404)
        self.assertEqual(self.client.get('/token/api/models/openrouter/endpoints', params={'model': 'v/m:batch'}).status_code, 400)
        with patch.object(w, 'list_model_endpoints', side_effect=RuntimeError('offline')):
            self.assertEqual(self.client.get('/token/api/models/openrouter/endpoints', params={'model': 'v/m'}).status_code, 503)
        with patch.object(w, 'list_model_endpoints', return_value=[]) as endpoints:
            self.client.get('/token/api/models/openrouter/endpoints', params={'model': 'v/m', 'extractor_id': 'x'})
        endpoints.assert_called_once_with('v/m', api_key=None)

    def test_typed_key_model_suggestions_never_store_or_echo_the_key(self):
        key = 'typed-secret-key-123'
        url = '/token/api/models'
        keyring = FakeKeyring()
        # Every request below runs with a credential store that records each use.
        with patch.object(credentials, '_keyring_module', return_value=keyring) as store:
            response = self.client.post(url, data={'provider': 'bad', 'api_key': key})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(key, response.text)
            response = self.client.post(url, data={'provider': 'gemini'})
            self.assertEqual(response.status_code, 400)
            self.assertIn('Enter an API key', response.json()['detail'])
            self.assertEqual(self.client.post(url, data={'provider': 'openai_compatible', 'api_key': key}).json(), [])
            with patch.object(w, 'list_live_models', return_value=[ModelOption('v/m', 'V M')]) as live:
                self.assertEqual(self.client.post(url, data={'provider': 'openrouter'}).json(), [{'id': 'v/m', 'name': 'V M'}])
            live.assert_called_once_with('', 'openrouter')
            with patch.object(w, 'list_live_models', return_value=[ModelOption('g', 'G')]) as live:
                response = self.client.post(url, data={'provider': 'gemini', 'api_key': f' {key} '})
            live.assert_called_once_with(key, 'gemini')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), [{'id': 'g', 'name': 'G'}])
            self.assertNotIn(key, response.text)
            for error in [RuntimeError(f'rejected key {key}'), ValueError(key), Exception(f'https://x/?key={key}')]:
                with self.subTest(error=type(error).__name__), patch.object(w, 'list_live_models', side_effect=error):
                    response = self.client.post(url, data={'provider': 'gemini', 'api_key': key})
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn(key, response.text)
                    self.assertIn('[redacted]', response.json()['detail'])
            with patch.object(w, 'list_live_models', side_effect=Exception(key)):
                self.assertEqual(self.client.post(url, data={'provider': 'gemini', 'api_key': key}).json()['detail'], '[redacted]')
            with patch.object(w, 'list_live_models', side_effect=Exception()):
                self.assertEqual(self.client.post(url, data={'provider': 'gemini', 'api_key': key}).json()['detail'], 'Models could not be loaded.')
            with patch.object(w, 'list_live_models', return_value=[]):
                self.assertEqual(self.client.post(url, data={'provider': 'gemini', 'api_key': key}).json(), [])
        store.assert_not_called()
        self.assertEqual((keyring.reads, keyring.writes, keyring.items), (0, 0, {}))
        config_dir = Path(self.tmp.name)
        for path in config_dir.rglob('*'):
            if path.is_file():
                self.assertNotIn(key, path.read_text(errors='ignore'), path)

    def test_upload_count_and_size_limits(self):
        response = self.client.post(self.base + '/uploads', files=[('files', ('a.pdf', b'%PDF'))] * 101)
        self.assertEqual(response.status_code, 413)
        with patch.object(w, 'save_uploads', side_effect=w.UploadTooLarge('too large')):
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
