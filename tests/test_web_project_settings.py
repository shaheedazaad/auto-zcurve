import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from auto_zcurve import web as w
from auto_zcurve.config import RunSettings, load_run_settings, save_run_settings
from auto_zcurve.projects import create_project


class WebProjectSettingsTests(unittest.TestCase):
    def test_save_clamps_defaults_and_preserves_run_preferences(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'XDG_CONFIG_HOME': tmp}):
            root = Path(tmp) / 'projects'
            project = create_project('Test', root=root)
            app = w.create_app(token='token', projects_root=root)
            url = '/token/projects/' + project.project_id + '/settings'
            with TestClient(app, base_url='http://127.0.0.1') as client:
                response = client.post(url, data={'model': 'gemini-test', 'parallel_requests': 99, 'request_delay_sec': -2})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual((response.json()['parallel_requests'], response.json()['request_delay_sec']), (32, 0))
                save_run_settings(project.path, RunSettings(primary_model='old', request_timeout_sec=123, max_upload_size_mb=40))
                response = client.post(url, data={'model': 'gemini-new'})
                self.assertEqual(response.status_code, 200)
                settings = load_run_settings(project.path)
                self.assertEqual((settings.primary_model, settings.request_timeout_sec, settings.max_upload_size_mb), ('gemini-new', 123, 40))
                for provider in ['bad', 'openrouter']:
                    with patch.object(app.state.runtime, '_load_key_from_store', return_value=None):
                        self.assertEqual(client.post(url, data={'provider': provider, 'model': 'vendor/model'}).status_code, 400)
                with patch.object(app.state.runtime, '_load_key_from_store', return_value='key'), patch.object(w, 'validate_model_option') as validate:
                    response = client.post(url, data={'provider': 'openrouter', 'model': 'vendor/model'})
                self.assertEqual(response.status_code, 200)
                validate.assert_called_once_with('openrouter', 'vendor/model', 'key')
                self.assertEqual(load_run_settings(project.path).provider, 'openrouter')
