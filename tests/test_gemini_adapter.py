from __future__ import annotations

import json
import builtins
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from auto_zcurve import gemini


class GeminiAdapterTests(unittest.TestCase):
    def test_metadata_aliases_missing_and_invalid_counts(self):
        self.assertEqual(gemini._response_usage({'usageMetadata': {'promptTokenCount': '12', 'candidatesTokenCount': 'bad', 'totalTokenCount': None}}), {'input_tokens': 12, 'output_tokens': None, 'total_tokens': None})
        self.assertEqual(gemini._response_usage(NS()), dict.fromkeys(['input_tokens', 'output_tokens', 'total_tokens']))
        self.assertIsNone(gemini._metadata_value({}, 'missing'))
        self.assertEqual(gemini._metadata_value(NS(second=3), 'first', 'second'), 3)
        self.assertIsNone(gemini._int_or_none([]))

    def test_empty_and_split_responses(self):
        for response in [NS(), NS(candidates=[NS(content=None)]), NS(candidates=[NS(content=NS(parts=[NS(text='  ')]))])]:
            with self.subTest(response=response), self.assertRaisesRegex(RuntimeError, 'empty text payload'):
                gemini._response_text(response)
        self.assertEqual(gemini._response_text(NS(candidates=[NS(content=NS(parts=[NS(text='{"effects":')])), NS(content=NS(parts=[NS(text='[]}')]))])), '{"effects":[]}')

    def test_json_parse_and_failed_repair_preserve_evidence(self):
        self.assertEqual(gemini._parse_json_response('{"effects":[]}'), ({'effects': []}, None))
        for repair in [Mock(return_value='still invalid'), Mock(side_effect=ValueError('unrepairable'))]:
            with patch('json_repair.repair_json', repair), self.assertRaisesRegex(RuntimeError, 'could not repair') as caught:
                gemini._parse_json_response('{broken')
            self.assertEqual(caught.exception.raw_response, '{broken')
            self.assertEqual(caught.exception.repaired_response, 'still invalid' if repair.side_effect is None else None)

    def test_generate_builds_schema_request_and_reads_usage(self):
        client = Mock()
        client.models.generate_content.return_value = NS(candidates=[NS(content=NS(parts=[NS(text='{"effects":[]}')]))], usage_metadata=NS(prompt_token_count=7, candidates_token_count=3, total_token_count=10))
        uploaded = object()
        result = gemini._generate(client, uploaded, 'models/gemini-test', 'extract', {'type': 'object'}, 'flex')
        kwargs = client.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs['model'], 'gemini-test')
        self.assertEqual(kwargs['contents'], ['extract', uploaded])
        self.assertEqual(kwargs['config'].response_mime_type, 'application/json')
        self.assertEqual(kwargs['config'].response_schema, {'type': 'object'})
        self.assertEqual(kwargs['config'].service_tier, 'flex')
        self.assertEqual(result, ({'effects': []}, '{"effects":[]}', None, {'input_tokens': 7, 'output_tokens': 3, 'total_tokens': 10}))

    def test_extraction_upload_names_results_and_validation_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            for filename, expected in [('article.pdf', 'article.pdf'), ('cafe\u0301.pdf', 'cafe.pdf'), ('研究', 'document.pdf')]:
                with self.subTest(filename=filename):
                    source = Path(tmp) / filename
                    source.write_bytes(b'%PDF-test')
                    client = Mock()
                    uploads = []
                    def upload(*, file, config):
                        uploads.append((file.read(), config))
                        return 'uploaded'
                    client.files.upload.side_effect = upload
                    data = {'effects': []}
                    kwargs = dict(source_path=source, source_name='nested/' + filename, api_key='secret', primary_model='models/gemini-test', response_schema={'type': 'object'}, schema_config=object(), instruction_path=Path(tmp) / 'instructions.md')
                    with patch('google.genai.Client', return_value=client) as constructor, patch.object(gemini, 'build_system_prompt', return_value='prompt'), patch.object(gemini, '_generate', return_value=(data, json.dumps(data), 'repaired', {'input_tokens': 5, 'output_tokens': 2, 'total_tokens': 7})), patch.object(gemini, 'validate_extracted_json') as validate, patch.object(gemini.time, 'monotonic', side_effect=[1, 1.25]):
                        result = gemini.extract_pdf(**kwargs)
                    constructor.assert_called_once_with(api_key='secret')
                    self.assertEqual(uploads, [(b'%PDF-test', {'display_name': expected, 'mime_type': 'application/pdf'})])
                    validate.assert_called_once_with(data, kwargs['schema_config'])
                    self.assertEqual((result.status, result.provider_used, result.model_used, result.input_mode), ('ok', 'gemini', 'gemini-test', 'native_pdf'))
                    self.assertEqual((result.input_tokens, result.output_tokens, result.total_tokens, result.duration_sec), (5, 2, 7, 0.25))
                    self.assertEqual(result.source_name, 'nested/' + filename)
                    self.assertEqual(result.raw_json, json.dumps(data))
                    self.assertEqual(result.repaired_response, 'repaired')
                    with patch('google.genai.Client', return_value=client), patch.object(gemini, 'build_system_prompt', return_value='prompt'), patch.object(gemini, '_generate', return_value=(data, 'raw', 'repaired', {})), patch.object(gemini, 'validate_extracted_json', side_effect=ValueError('schema mismatch')):
                        with self.assertRaisesRegex(ValueError, 'schema mismatch') as caught:
                            gemini.extract_pdf(**kwargs)
                        self.assertEqual(caught.exception.raw_response, 'raw')
                        self.assertEqual(caught.exception.repaired_response, 'repaired')

    def test_missing_sdk_reports_actionable_error(self):
        original_import = builtins.__import__

        def import_without_google_genai(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 'google' and 'genai' in fromlist:
                raise ImportError('simulated missing google.genai')
            return original_import(name, globals, locals, fromlist, level)

        with patch('builtins.__import__', side_effect=import_without_google_genai), self.assertRaisesRegex(RuntimeError, 'google-genai is not installed'):
            gemini.extract_pdf(source_path=Path('a.pdf'), source_name='a.pdf', api_key='key', primary_model='gemini', response_schema={}, schema_config=object(), instruction_path=Path('prompt'))
