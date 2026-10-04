import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from auto_zcurve import artifacts as a
from auto_zcurve.llm import ExtractionResult


class ArtifactRecoveryTests(unittest.TestCase):
    def test_atomic_write_failure_preserves_previous_data_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'data.json'
            path.write_text('["old"]')
            with patch.object(a.os, 'replace', side_effect=OSError('denied')), self.assertRaises(OSError):
                a._atomic_json_dump(path, ['new'])
            self.assertEqual(json.loads(path.read_text()), ['old'])
            self.assertEqual(list(Path(tmp).iterdir()), [path])

    def test_corrupt_backup_names_do_not_overwrite_existing_backups(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(a, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
            path = Path(tmp) / 'extractions.json'
            first = a._corrupt_backup_path(path)
            first.touch()
            second = a._corrupt_backup_path(path)
            second.touch()
            third = a._corrupt_backup_path(path)
            self.assertTrue(second.name.endswith('-2.json'))
            self.assertTrue(third.name.endswith('-3.json'))

    def test_recovery_ignores_non_records_and_unreadable_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            a.ensure_output_dirs(project)
            for name, content in [('a.json', '{"source_name":"a.pdf","status":"ok"}'), ('b.json', 'broken'), ('c.json', '[]'), ('d.json', '{"source_name":" "}'), ('e.provider-response.json', '{"source_name":"ignored.pdf"}')]:
                (a.raw_dir(project) / name).write_text(content)
            (a.raw_dir(project) / 'f.json').write_bytes(b'\xff')
            (a.raw_dir(project) / 'g.json').mkdir()
            aggregate = a.output_dir(project) / 'extractions.json'
            aggregate.write_text('broken')
            with patch.object(Path, 'replace', side_effect=OSError('read only')):
                records = a.load_extractions(project)
            self.assertEqual(records, [{'source_name': 'a.pdf', 'status': 'ok'}])
            self.assertEqual(aggregate.read_text(), 'broken')
            aggregate.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Expected a JSON array'):
                a.load_extractions(project)

    def test_upsert_replaces_matching_record_without_losing_other_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            for source in ['a.pdf', 'b.pdf']:
                a.upsert_extraction(project, ExtractionResult(project / source, source, 'error'))
            result = ExtractionResult(project / 'b.pdf', 'b.pdf', 'ok', data={'effects': [{}]}, raw_response='raw', repaired_response='repaired', provider_responses=[{'attempt': 1}])
            records = a.upsert_extraction(project, result)
            self.assertEqual([(record['source_name'], record['status']) for record in records], [('a.pdf', 'error'), ('b.pdf', 'ok')])
            self.assertEqual(a.load_extractions(project), records)
            self.assertEqual(json.loads(a.raw_path(project, 'b.pdf').read_text()), records[1])
            self.assertEqual(a.raw_response_path(project, 'b.pdf').read_text(), 'raw')
            self.assertEqual(a.repaired_response_path(project, 'b.pdf').read_text(), 'repaired')

    def test_deletion_removes_artifacts_and_reports_but_not_external_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            external = root / 'keep.txt'
            external.write_text('keep')
            a.ensure_output_dirs(project)
            extra = project / 'output/custom-response.txt'
            extra.write_text('response')
            a.save_extractions(project, [{'source_name': 'a.pdf', 'provider_response_path': '../keep.txt', 'raw_response_path': 'output/custom-response.txt'}, {'source_name': 'b.pdf'}])
            for path in [a.raw_path(project, 'a.pdf'), a.provider_response_path(project, 'a.pdf'), a.raw_response_path(project, 'a.pdf'), a.repaired_response_path(project, 'a.pdf')]:
                path.write_text('artifact')
            reports = ['report.html', 'zcurve_summary.txt', 'zcurve_plot.png', 'zcurve_reproduction_settings.csv', 'disclosure_table.csv']
            for name in reports:
                (a.output_dir(project) / name).write_text('report')
            self.assertTrue(a.delete_extraction(project, 'a.pdf'))
            self.assertFalse(a.delete_extraction(project, 'a.pdf'))
            self.assertEqual(a.load_extractions(project), [{'source_name': 'b.pdf'}])
            self.assertEqual(external.read_text(), 'keep')
            self.assertFalse(extra.exists())
            self.assertFalse(a.raw_path(project, 'a.pdf').exists())
            self.assertTrue(all(not (a.output_dir(project) / name).exists() for name in reports))

    def test_deletion_tolerates_missing_or_unremovable_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            a.save_extractions(project, [{'source_name': 'a.pdf'}, {'source_name': 'b.pdf'}])
            a.raw_path(project, 'a.pdf').mkdir()
            self.assertTrue(a.delete_extraction(project, 'a.pdf'))
            self.assertEqual(a.load_extractions(project), [{'source_name': 'b.pdf'}])
            self.assertTrue(a.raw_path(project, 'a.pdf').is_dir())

    def test_summary_parser_ignores_missing_metrics(self):
        self.assertEqual(a.parse_zcurve_summary('Unrecognized output'), {'execution': None, 'metrics': []})
        parsed = a.parse_zcurve_summary('ERR .5 -.1 1.\nBootstrap execution: parallel')
        self.assertEqual(parsed['execution'], 'Bootstrap execution: parallel')
        self.assertEqual([(m['code'], m['estimate'], m['lower_ci'], m['upper_ci']) for m in parsed['metrics']], [('ERR', '.5', '-.1', '1.')])
