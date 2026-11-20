import builtins
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from auto_zcurve import models as m


class ModelDiscoveryTests(unittest.TestCase):
    def test_gemini_models_are_used_as_entered(self):
        self.assertEqual(m.validate_model('gemini', 'models/a', 'key'), 'a')
        with self.assertRaisesRegex(ValueError, 'name is required'):
            m.normalize_model_name(' ')

    def test_live_gemini_filtering_sorting_and_deduplication(self):
        client = Mock()
        client.models.list.return_value = [NS(name=None), {'name': 'models/ignored', 'supportedActions': ['embedContent']}, {'name': 'models/z-last', 'displayName': 'Last', 'description': 'details', 'supportedActions': ['GENERATECONTENT']}, NS(name='models/a-first', supported_actions=[]), {'name': 'models/b-second'}, {'name': 'models/z-last', 'displayName': 'duplicate'}]
        with patch('google.genai.Client', return_value=client) as constructor:
            result = m._list_gemini_models('key')
        constructor.assert_called_once_with(api_key='key')
        self.assertEqual([option.name for option in result], ['a-first', 'b-second', 'z-last'])
        self.assertEqual((result[-1].display_name, result[-1].description), ('Last', 'details'))
        with patch.object(m, '_list_gemini_models', return_value=result):
            self.assertEqual(m.list_live_models('key'), result)

    def test_missing_sdk(self):
        real_import = builtins.__import__
        def importing(name, *args, **kwargs):
            if name == 'google':
                raise ImportError('missing')
            return real_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=importing), self.assertRaisesRegex(RuntimeError, 'google-genai is not installed'):
            m._list_gemini_models('key')

    def test_openai_compatible_has_no_catalog(self):
        self.assertEqual(m.list_live_models('key', 'openai_compatible'), [])
        self.assertEqual(m.resolve_input_mode('openai_compatible', m.ModelOption('x', 'x'), 'anything'), 'local_text')

    def test_openrouter_catalog_suggestions_skip_unnamed_entries(self):
        catalog = [{'id': 'b/model'}, {'id': ''}, {'id': 'A/model', 'name': 'A'}]
        with patch('auto_zcurve.openrouter.list_models', return_value=catalog) as listing:
            options = m.list_live_models('', 'openrouter', timeout_sec=5)
        listing.assert_called_once_with(api_key=None, timeout_sec=5)
        self.assertEqual([(option.name, option.display_name) for option in options], [('A/model', 'A'), ('b/model', 'b/model')])

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

    def test_output_limit_is_the_extractors_own_else_the_openrouter_endpoints(self):
        with patch('auto_zcurve.openrouter.endpoint_output_limit', return_value=393_216) as lookup:
            self.assertEqual(m.resolve_max_output_tokens('openrouter', 'v/m', 8_000, 'key'), 8_000)
            for provider in ('gemini', 'openai_compatible'):
                self.assertIsNone(m.resolve_max_output_tokens(provider, 'model', None, 'key'))
            lookup.assert_not_called()
            limit = m.resolve_max_output_tokens(
                'openrouter', 'v/m', None, '', openrouter_provider='deepinfra/fp8', timeout_sec=9,
            )
        self.assertEqual(limit, 393_216)
        lookup.assert_called_once_with(
            'v/m', preferred_provider='deepinfra/fp8', api_key=None, timeout_sec=9,
        )

    def test_custom_endpoint_validation(self):
        option = m.validate_model_option('openai_compatible', 'local', '', context_length=4096)
        self.assertEqual((option.name, option.provider, option.input_mode, option.context_length), ('local', 'openai_compatible', 'local_text', 4096))
