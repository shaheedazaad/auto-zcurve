import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from fastapi.testclient import TestClient
from auto_zcurve import credentials
from auto_zcurve import web as w
from auto_zcurve.config import RunSettings, save_run_settings
from auto_zcurve.extractors import create_extractor, delete_extractor, set_key_saved
from tests.test_key_vault import FakeKeyring
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

    def test_missing_resource_routes_and_cancel_without_job(self):
        missing = '/token/projects/' + 'f' * 16
        for suffix in ['/events', '/report', '/zcurve-plot', '/results.zip']:
            self.assertEqual(self.client.get(missing + suffix).status_code, 404)
        self.assertEqual(self.client.post(missing + '/open-folder').status_code, 404)
        for suffix in ['/report', '/zcurve-plot']:
            self.assertEqual(self.client.get(self.base + suffix).status_code, 404)
        self.assertEqual(self.client.post(self.base + '/cancel').status_code, 409)
        self.assertEqual(self.client.post(missing + '/regenerate-report').status_code, 409)

    def extractor(self, **values):
        return create_extractor({'name': 'Router', 'provider': 'openrouter', 'model': 'vendor/model', **values})

    def test_run_and_retry_use_the_extractor_routing_and_endpoint_listing_uses_its_session_key(self):
        router = self.extractor(openrouter_provider='deepinfra/fp8', openrouter_allow_fallbacks='deny', reasoning_effort='low')
        unavailable = credentials.CredentialStoreUnavailable('no keyring')
        with patch.object(credentials, '_keyring_module', side_effect=unavailable):
            self.assertFalse(self.runtime.vault.set(router.id, 'session-key'))
        with patch.object(self.runtime, 'start_job', return_value=NS(status='queued')) as start:
            for action in ['run', 'retry']:
                response = self.client.post(self.base + '/' + action, data={'extractor_id': router.id})
                self.assertEqual(response.status_code, 202, response.text)
                settings, key = start.call_args.args[2], start.call_args.args[3]
                self.assertEqual((settings.openrouter_provider, settings.openrouter_allow_fallbacks, settings.reasoning_effort), ('deepinfra/fp8', False, 'low'))
                self.assertEqual(key, 'session-key')
        listing = [{'tag': 'deepinfra/fp8', 'name': 'DeepInfra (fp8)'}]
        with patch.object(w, 'list_model_endpoints', return_value=listing) as endpoints:
            response = self.client.get('/token/api/models/openrouter/endpoints', params={'model': 'vendor/model', 'extractor_id': router.id})
        self.assertEqual((response.status_code, response.json()), (200, listing))
        endpoints.assert_called_once_with('vendor/model', api_key='session-key')

    def test_keyless_endpoint_runs_and_unknown_or_deleted_extractors_are_refused(self):
        local = create_extractor({'name': 'Local', 'provider': 'openai_compatible', 'model': 'q', 'base_url': 'http://h/v1'})
        with patch.object(self.runtime, 'start_job', return_value=NS(status='queued')) as start:
            self.assertEqual(self.client.post(self.base + '/run', data={'extractor_id': local.id}).status_code, 202)
        self.assertIsNone(start.call_args.args[3])
        self.assertEqual(start.call_args.args[2].base_url, 'http://h/v1')
        response = self.client.post(self.base + '/run', data={'extractor_id': 'missing'})
        self.assertEqual(response.status_code, 409)
        self.assertIn('no longer exists', response.text)
        delete_extractor(local.id)
        response = self.client.post(self.base + '/retry')
        self.assertEqual(response.status_code, 409)
        self.assertIn('was deleted', response.text)

    def test_unreadable_saved_keys_stop_the_run(self):
        router = self.extractor()
        set_key_saved(router.id, True)
        keyring = FakeKeyring({('auto-zcurve', 'extractor_keys'): '{broken'})
        with patch.object(credentials, '_keyring_module', return_value=keyring):
            response = self.client.post(self.base + '/run', data={'extractor_id': router.id})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Couldn't unlock saved keys", response.text)

    def test_an_extractor_without_a_model_cannot_run(self):
        local = create_extractor({'name': 'Local', 'provider': 'openai_compatible', 'model': 'q', 'base_url': 'http://h/v1'})
        from auto_zcurve.extractors import load_extractors, save_extractors
        from dataclasses import replace
        save_extractors([replace(item, model='') for item in load_extractors()])
        response = self.client.post(self.base + '/run', data={'extractor_id': local.id})
        self.assertEqual(response.status_code, 409)
        self.assertIn('Choose a model', response.text)

    def test_report_regeneration_reuses_the_last_run_settings(self):
        save_run_settings(self.project.path, RunSettings(primary_model='vendor/old', provider='openrouter'))
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

    def test_several_responses_are_deleted_at_once(self):
        from auto_zcurve.artifacts import load_extractions, save_extractions
        save_extractions(self.project.path, [{'source_name': name, 'status': 'ok'} for name in ['a.pdf', 'b.xml', 'c.pdf']])
        response = self.client.post(self.base + '/responses/delete', data={'source_names': json.dumps(['a.pdf', 'b.xml', 'missing.pdf'])})
        self.assertEqual(response.json(), {'deleted': True, 'source_names': ['a.pdf', 'b.xml']})
        self.assertEqual([item['source_name'] for item in load_extractions(self.project.path)], ['c.pdf'])
        response = self.client.post(self.base + '/responses/delete', data={'source_names': json.dumps(['a.pdf', 'b.xml'])})
        self.assertEqual(response.status_code, 404)
        self.assertIn('None of those sources', response.text)
        for bad in [json.dumps(['c.pdf', '../x.pdf']), 'not json', json.dumps('c.pdf'), json.dumps([1]), json.dumps([])]:
            response = self.client.post(self.base + '/responses/delete', data={'source_names': bad})
            self.assertEqual(response.status_code, 400, bad)
        self.assertEqual(len(load_extractions(self.project.path)), 1)

    def test_retry_passes_only_the_chosen_sources(self):
        router = create_extractor({'name': 'Local', 'provider': 'openai_compatible', 'model': 'q', 'base_url': 'http://h/v1'})
        with patch.object(self.runtime, 'start_job', return_value=NS(status='queued')) as start:
            response = self.client.post(self.base + '/retry', data={'extractor_id': router.id, 'source_names': json.dumps(['a.pdf', 'b.xml', 'a.pdf'])})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(start.call_args.args[4], ['a.pdf', 'b.xml'])
            self.client.post(self.base + '/retry', data={'extractor_id': router.id})
            self.assertIsNone(start.call_args.args[4])
            response = self.client.post(self.base + '/retry', data={'source_names': json.dumps(['notes.txt'])})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(start.call_count, 2)

    def test_a_selection_larger_than_the_form_field_limit_is_accepted(self):
        from auto_zcurve.artifacts import load_extractions, save_extractions
        names = [f'paper-{index:04}.pdf' for index in range(1500)]
        save_extractions(self.project.path, [{'source_name': name, 'status': 'ok'} for name in names])
        response = self.client.post(self.base + '/responses/delete', files={'source_names': (None, json.dumps(names))})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['source_names']), 1500)
        self.assertEqual(load_extractions(self.project.path), [])

    def test_minimal_zip_and_folder_open_failure(self):
        (self.project.path / 'extraction_schema.yml').unlink()
        shutil.rmtree(self.project.path / 'output')
        response = self.client.get(self.base + '/results.zip')
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.namelist(), [])
        with patch.object(w.platform, 'system', return_value='Windows'), patch.object(w.os, 'startfile', side_effect=OSError('not available'), create=True):
            self.assertEqual(self.client.post(self.base + '/open-folder').status_code, 503)

    def test_edits_without_results_do_not_reset_and_invalid_host_ports_are_rejected(self):
        for suffix, payload in [('/instructions', {'instructions': 'new instructions'}), ('/schema', {'schema_text': 'effects: {claim: {type: string}}'})]:
            response = self.client.post(self.base + suffix, data=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()['reset'])
        for host in ['localhost:invalid', 'localhost:80:90', '127.0.0.1:']:
            self.assertEqual(self.client.get('/token/', headers={'host': host}).status_code, 400)
