from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from auto_zcurve import schema as s


class SchemaValidationTests(unittest.TestCase):
    def test_invalid_schema_structures(self):
        cases = [('effects: {x: string}', 'must be a mapping in YAML'), ('effects: {x: {type: array, items_type: array}}', 'array of arrays'), ('{}', 'at least one field'), ('effects: []', 'must be a mapping'), ('effects: {}', 'at least one field'), ('effects: {" ": {type: string}}', 'must be named'), ('[one, two]', 'top-level mapping')]
        for text, message in cases:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                s.parse_extraction_schema(text)
        with patch('yaml.safe_load', side_effect=yaml.YAMLError('bad')), self.assertRaisesRegex(ValueError, 'Invalid YAML: The YAML could not be parsed'):
            s.parse_extraction_schema('bad')
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(FileNotFoundError, 'Schema file not found'):
            s.read_extraction_schema(Path(tmp) / 'missing.yml')

    def test_response_schema_required_arrays_and_descriptions(self):
        config = s.parse_extraction_schema('name: custom\ndescription: 42\nmeta_data:\n  id: {type: integer, required: true}\neffects:\n  tags: {type: array, items_type: string, description: labels}\n  value: {type: number, required: true}')
        response = s.build_response_schema(config)
        self.assertEqual(config.description, '42')
        self.assertEqual(response['required'], ['meta_data', 'effects'])
        self.assertEqual(response['properties']['meta_data']['required'], ['id'])
        effect = response['properties']['effects']['items']
        self.assertEqual(effect['required'], ['value'])
        self.assertEqual(effect['properties']['tags'], {'type': 'array', 'items': {'type': 'string'}, 'description': 'labels'})
        config.effects = {}
        with self.assertRaisesRegex(ValueError, 'at least one effect field'):
            s.build_response_schema(config)

    def test_scalar_type_contracts(self):
        cases = [('string', ['text', ''], [1, True, [], {}]), ('number', [1, 1.5, -2], ['1', [], {}, True, False]), ('integer', [0, -4], [1.5, '1', True, False]), ('boolean', [True, False], [0, 1, 'true'])]
        for kind, valid, invalid in cases:
            config = s.parse_extraction_schema(f'effects: {{value: {{type: {kind}}}}}')
            for value in [None, *valid]:
                with self.subTest(kind=kind, valid=value):
                    s.validate_extracted_json({'effects': [{'value': value}]}, config)
            for value in invalid:
                with self.subTest(kind=kind, invalid=value), self.assertRaisesRegex(ValueError, r'effects\[1\].value must be'):
                    s.validate_extracted_json({'effects': [{'value': value}]}, config)

    def test_array_elements_and_invalid_payload_shapes(self):
        config = s.parse_extraction_schema('effects: {values: {type: array, items: {type: integer}}}')
        s.validate_extracted_json({'effects': [{'values': [1, None, 3]}]}, config)
        for value, message in [({'effects': [{'values': 1}]}, 'must be an array'), ({'effects': [{'values': [1, 'bad']}]}, r'values\[2\] must be an integer'), ([], 'Provider returned JSON'), ({'effects': [], 'meta_data': []}, 'must be a JSON object when present'), ({}, 'effects.*array'), ({'effects': [None]}, 'must be a JSON object')]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                s.validate_extracted_json(value, config, provider_name='Provider')
        config = s.parse_extraction_schema('meta_data: {title: {type: string}}\neffects: {value: {type: number}}')
        with self.assertRaisesRegex(ValueError, 'Missing top-level `meta_data`'):
            s.validate_extracted_json({'effects': []}, config)

    def test_normalization_leaves_ambiguous_shapes_for_validation(self):
        config = s.parse_extraction_schema('meta_data: {title: {type: string}}\neffects: {text: {type: string}, tags: {type: array, items_type: string}, count: {type: integer}}')
        for value in [None, [], 'bad', 3]:
            self.assertIs(s.normalize_extracted_json(value, config), value)
        payload = {'meta_data': None, 'effects': [None, {'text': {'bad': 1}, 'tags': 'bad', 'count': '3'}, {'text': True, 'tags': [False, None, {}]}, {}]}
        s.normalize_extracted_json(payload, config)
        self.assertEqual(payload['effects'][1], {'text': {'bad': 1}, 'tags': 'bad', 'count': '3'})
        self.assertEqual(payload['effects'][2], {'text': 'true', 'tags': ['false', None, {}]})
        self.assertEqual(s.normalize_extracted_json({'effects': 'bad'}, config), {'effects': 'bad'})
