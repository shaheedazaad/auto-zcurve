import builtins
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from auto_zcurve import models as m


class ModelDiscoveryTests(unittest.TestCase):
    def test_allowlist_errors_and_request_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'models.yml'
            self.assertIsNone(m._configured_models(path))
            cases = [('[', 'Invalid model allowlist YAML'), ('[]', 'top-level'), ('models: []', 'top-level'), ('models: {gemini: bad}', 'must be a list'), ('models: {gemini: [2]}', 'model ID or mapping'), ('models: {gemini: [{id: a, parallel_requests: bad}]}', 'invalid request defaults'), ('models: {gemini: [{id: a, endpoints: text}]}', 'endpoint tags'), ('models: {gemini: [{id: a, endpoints: [null]}]}', 'endpoint tags'), ('models: {gemini: [{id: a, endpoints: [" "]}]}', 'endpoint tags'), ('models: {gemini: [{id: a, endpoints: [tag]}]}', 'cannot define endpoints')]
            for text, error in cases:
                path.write_text(text)
                with self.subTest(text=text), self.assertRaisesRegex(ValueError, error):
                    m._configured_models(path)
            path.write_text('models: {gemini: [{id: a, parallel_requests: 99, request_delay_sec: -5}, {id: b, parallel_requests: 0, request_delay_sec: 9999}]}')
            self.assertEqual(m._configured_models(path), {'gemini': {'a': ((), 32, 0), 'b': ((), 1, 3600)}})
            with patch.object(Path, 'read_text', side_effect=OSError('denied')), self.assertRaisesRegex(RuntimeError, 'Could not read model allowlist'):
                m._configured_models(path)

    def test_no_allowlist_and_missing_provider(self):
        options = [m.ModelOption('a', 'A')]
        with patch.object(m, '_configured_models', return_value=None):
            self.assertIs(m._apply_model_allowlist('gemini', options), options)
            self.assertEqual(m.validate_model('gemini', 'models/a', 'key'), 'a')
        with patch.object(m, '_configured_models', return_value={}):
            self.assertEqual(m._apply_model_allowlist('gemini', options), [])
            with self.assertRaisesRegex(ValueError, 'not allowed'):
                m.validate_model_option('gemini', 'a', 'key')
        with self.assertRaisesRegex(ValueError, 'name is required'):
            m.normalize_model_name(' ')

    def test_live_gemini_filtering_sorting_and_deduplication(self):
        client = Mock()
        client.models.list.return_value = [NS(name=None), {'name': 'models/ignored', 'supportedActions': ['embedContent']}, {'name': 'models/z-last', 'displayName': 'Last', 'description': 'details', 'supportedActions': ['GENERATECONTENT']}, NS(name='models/a-first', supported_actions=[]), {'name': 'models/' + m.MAIN_MODELS[1]}, {'name': 'models/' + m.MAIN_MODELS[0]}, {'name': 'models/z-last', 'displayName': 'duplicate'}]
        with patch('google.genai.Client', return_value=client) as constructor:
            result = m._list_gemini_models('key')
        constructor.assert_called_once_with(api_key='key')
        self.assertEqual([option.name for option in result], [m.MAIN_MODELS[0], m.MAIN_MODELS[1], 'a-first', 'z-last'])
        self.assertEqual((result[-1].display_name, result[-1].description), ('Last', 'details'))
        with patch.object(m, '_list_gemini_models', return_value=result), patch.object(m, '_configured_models', return_value=None):
            self.assertEqual(m.list_live_models('key'), result)

    def test_missing_sdk(self):
        real_import = builtins.__import__
        def importing(name, *args, **kwargs):
            if name == 'google':
                raise ImportError('missing')
            return real_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=importing), self.assertRaisesRegex(RuntimeError, 'google-genai is not installed'):
            m._list_gemini_models('key')

    def test_manual_providers_have_no_discovery_or_fallback(self):
        for provider in ['openrouter', 'openai_compatible']:
            self.assertEqual(m.list_live_models('key', provider), [])
            self.assertEqual(m.fallback_models(provider), [])
            self.assertEqual(m.model_request_defaults('custom', provider), (1, 0))
        with patch.object(m, 'fallback_models', return_value=[]):
            self.assertEqual(m.model_request_defaults('unknown'), (1, 30))
        self.assertEqual(m.resolve_input_mode('openai_compatible', m.ModelOption('x', 'x'), 'anything'), 'local_text')

    def test_openrouter_validation_failures_and_success(self):
        good = {'id': 'vendor/model', 'name': 'Display', 'supported_parameters': ['STRUCTURED_OUTPUTS'], 'architecture': {'input_modalities': ['TEXT']}, 'context_length': 123, 'reasoning': True}
        for record, error in [(None, 'was not found'), ({**good, 'supported_parameters': []}, 'strict structured outputs'), ({**good, 'architecture': None}, 'does not accept text'), ({**good, 'architecture': {'input_modalities': ['image']}}, 'does not accept text')]:
            with self.subTest(record=record), patch('auto_zcurve.openrouter.list_models', return_value=[] if record is None else [record]), self.assertRaisesRegex(ValueError, error):
                m.validate_model_option('openrouter', 'vendor/model', 'key')
        with patch('auto_zcurve.openrouter.list_models', return_value=[good]), patch('auto_zcurve.openrouter.model_input_mode', return_value='unsupported'), self.assertRaisesRegex(ValueError, 'not compatible'):
            m.validate_model_option('openrouter', 'vendor/model', 'key')
        with patch('auto_zcurve.openrouter.list_models', return_value=[good]) as catalog, patch('auto_zcurve.openrouter.model_input_mode', return_value='native_pdf'):
            option = m.validate_model_option('openrouter', 'vendor/model', 'key', timeout_sec=9)
        catalog.assert_called_once_with(api_key='key', timeout_sec=9, structured_outputs_only=False)
        self.assertEqual((option.display_name, option.context_length, option.supports_reasoning, option.input_mode), ('Display', 123, True, 'cloudflare_pdf'))

    def test_custom_endpoint_validation(self):
        with patch('auto_zcurve.config.load_app_settings', return_value=NS(openai_base_url='http://localhost:8080/v1')), patch('auto_zcurve.openai_compatible.normalize_base_url') as normalize:
            option = m.validate_model_option('openai_compatible', 'local', '')
            normalize.assert_called_once_with('http://localhost:8080/v1')
            self.assertEqual((option.name, option.provider, option.input_mode, option.request_delay_sec), ('local', 'openai_compatible', 'local_text', 0))
