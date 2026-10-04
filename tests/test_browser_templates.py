import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from auto_zcurve import web
from auto_zcurve.projects import create_project


class BrowserTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.node = shutil.which('node')
        if not cls.node:
            raise unittest.SkipTest('Node.js is required for template integration tests')
        available = subprocess.run([cls.node, '-e', "require('jsdom')"], cwd=cls.root, capture_output=True)
        if available.returncode:
            raise unittest.SkipTest('Run npm ci to install browser test dependencies')

    def test_rendered_pages_work_with_the_real_client_script(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            isolated_config = str(root / 'config')
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': isolated_config, 'APPDATA': isolated_config}), patch.object(web, 'saved_api_key_configured', return_value=False):
                project = create_project('Template integration', root=root / 'projects')
                (project.path / 'sources' / "A 'quoted' paper.pdf").write_bytes(b'%PDF-1.4')
                app = web.create_app(token='token', projects_root=root / 'projects')
                with TestClient(app, base_url='http://127.0.0.1') as client:
                    responses = {}
                    for route in ['/token/api/projects', f'/token/api/projects/{project.project_id}', '/token/api/models/gemini']:
                        response = client.get(route)
                        self.assertEqual(response.status_code, 200, response.text)
                        responses[route] = response.json()
                    for page, path in [('home', '/token/'), ('settings', '/token/settings'), ('project', f'/token/projects/{project.project_id}')]:
                        with self.subTest(page=page):
                            response = client.get(path)
                            self.assertEqual(response.status_code, 200, response.text)
                            result = subprocess.run([self.node, 'tests/js/check-rendered-page.cjs'], input=json.dumps({'html': response.text, 'path': path, 'page': page, 'responses': responses}), text=True, capture_output=True, cwd=self.root, timeout=30)
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
