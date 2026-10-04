from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_zcurve import env, providers
from auto_zcurve.llm import ExtractionResult
from auto_zcurve.prompts import build_system_prompt, render_text_template
from auto_zcurve.security import redact_secrets


class EnvironmentTests(unittest.TestCase):
    def test_dotenv_parsing_and_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"EXISTING": "keep"}, clear=True):
            path = Path(tmp) / '.env'
            env.load_dotenv(path)
            self.assertEqual(dict(os.environ), {"EXISTING": "keep"})
            path.write_text('# comment\n\nexport TOKEN = "a=b"\nINVALID\n=ignore\nEXISTING=replace\nEMPTY=\nSINGLE=\'value\'\n')
            env.load_dotenv(path)
            self.assertEqual(dict(os.environ), {"EXISTING": "keep", "TOKEN": "a=b", "EMPTY": "", "SINGLE": "value"})
            env.load_dotenv(path, overwrite=True)
            self.assertEqual(os.environ['EXISTING'], 'replace')

    def test_key_precedence_for_every_provider(self):
        for provider, definition in providers.PROVIDER_REGISTRY.items():
            with self.subTest(provider=provider), patch.dict(os.environ, {}, clear=True), patch.object(env, 'load_dotenv') as dotenv, patch.object(env, 'load_saved_api_key', return_value='saved') as saved:
                self.assertEqual(env.resolve_api_key(explicit_key=' explicit ', provider=provider), 'explicit')
                dotenv.assert_not_called()
                saved.assert_not_called()
                os.environ[definition.environment_key] = ' environment '
                self.assertEqual(env.resolve_api_key(Path('/project'), provider=provider), 'environment')
                self.assertEqual(dotenv.call_args_list[-2].args, (Path('/project/.env'),))
                saved.assert_not_called()
                os.environ.clear()
                self.assertEqual(env.resolve_api_key(provider=provider), 'saved')
                saved.assert_called_once_with(provider)
                saved.return_value = None
                if provider == 'openai_compatible':
                    self.assertEqual(env.resolve_api_key(provider=provider), '')
                else:
                    with self.assertRaisesRegex(RuntimeError, definition.environment_key):
                        env.resolve_api_key(provider=provider)

    def test_project_dotenv_wins_over_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), patch.object(env.Path, 'cwd', return_value=Path(tmp)), patch.object(env, 'load_saved_api_key') as saved:
            project = Path(tmp) / 'project'
            project.mkdir()
            (project / '.env').write_text('GEMINI_API_KEY=project-key')
            (Path(tmp) / '.env').write_text('GEMINI_API_KEY=working-key')
            self.assertEqual(env.resolve_api_key(project), 'project-key')
            saved.assert_not_called()


class ProviderTests(unittest.TestCase):
    def test_normalization_labels_and_invalid_provider(self):
        self.assertEqual(providers.normalize_provider(None), 'gemini')
        for name, definition in providers.PROVIDER_REGISTRY.items():
            self.assertEqual(providers.normalize_provider(' ' + name.upper() + ' '), name)
            self.assertEqual(providers.provider_label(name), definition.label)
            self.assertIs(providers.provider_definition(name), definition)
        with self.assertRaisesRegex(ValueError, 'Unsupported LLM provider'):
            providers.normalize_provider('unknown')

    def test_dispatch_preserves_request_and_gemini_service_tier(self):
        for provider, definition in providers.PROVIDER_REGISTRY.items():
            with self.subTest(provider=provider):
                implementation = Mock(return_value=object())
                request = dict(source_path=Path('article.pdf'), source_name='article.pdf', api_key='key', primary_model='model', response_schema={'type': 'object'}, schema_config=object(), instruction_path=Path('instructions.md'), request_timeout_sec=30, input_mode='text', context_length=1234, project_dir=Path('project'), reasoning_effort='high', endpoint_order=('endpoint',))
                with patch.object(providers.importlib, 'import_module', return_value=SimpleNamespace(extract_pdf=implementation)) as load:
                    result = providers.extract_pdf(provider=provider, service_tier='flex', **request)
                load.assert_called_once_with(definition.adapter_module)
                expected = dict(request)
                if provider == 'gemini':
                    expected['service_tier'] = 'flex'
                implementation.assert_called_once_with(**expected)
                self.assertIs(result, implementation.return_value)


class PromptAndResultTests(unittest.TestCase):
    def test_template_only_replaces_named_placeholders(self):
        self.assertEqual(render_text_template('{{known}} {{unknown}} {known}', {'known': 'value'}), 'value {{unknown}} {known}')

    def test_prompt_role_names_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'instructions.md'
            path.write_text('{{reported_statistic_field}} {{eligible_field}} {{eligibility_explanation_field}}')
            cases = [({}, 'reported_statistic eligible eligibility_explanation'), ({'reported_test': 'test'}, 'test eligible eligibility_explanation'), ({'reported_statistic': 'stat', 'reported_test': 'test', 'eligible': 'include', 'eligibility_explanation': 'why'}, 'stat include why')]
            for roles, expected in cases:
                with self.subTest(roles=roles), patch('auto_zcurve.prompts.build_role_lookup', return_value={'effect': roles}):
                    self.assertEqual(build_system_prompt(object(), path), expected)

    def test_effect_count_rejects_non_lists(self):
        for data, count in [(None, 0), ({}, 0), ({'effects': None}, 0), ({'effects': 'bad'}, 0), ({'effects': {}}, 0), ({'effects': []}, 0), ({'effects': [{}, {}]}, 2)]:
            with self.subTest(data=data):
                self.assertEqual(ExtractionResult(Path('a.pdf'), 'a.pdf', 'ok', data=data).effect_count, count)

    def test_redaction_handles_blank_secrets_and_repeated_occurrences(self):
        self.assertEqual(redact_secrets('token / token / other', [None, '', '  ', ' token ']), '[redacted] / [redacted] / other')
        self.assertEqual(redact_secrets(123, []), '123')
