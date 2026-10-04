import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_zcurve import projects as p
from auto_zcurve.artifacts import save_extractions


class ProjectValidationTests(unittest.TestCase):
    def test_platform_data_directories(self):
        cases = [('linux', {}, '/home/test/.local/share/auto-zcurve'), ('linux', {'XDG_DATA_HOME': '/data'}, '/data/auto-zcurve'), ('darwin', {}, '/home/test/Library/Application Support/Auto Z-Curve'), ('win32', {}, '/home/test/AppData/Local/Auto Z-Curve'), ('win32', {'LOCALAPPDATA': '/data'}, '/data/Auto Z-Curve'), ('linux', {'AUTO_ZCURVE_HOME': '/custom'}, '/custom')]
        for platform, env, expected in cases:
            with self.subTest(platform=platform, env=env), patch.object(p.sys, 'platform', platform), patch.dict(os.environ, env, clear=True), patch.object(Path, 'home', return_value=Path('/home/test')):
                self.assertEqual(p.app_data_dir(), Path(expected))
                self.assertEqual(p.projects_dir(), Path(expected) / 'projects')

    def test_names_and_metadata_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in [' ', 'a' * 101]:
                with self.assertRaises(p.ProjectError):
                    p.create_project(name, root=root)
            project = p.create_project(' A  study ', root=root)
            self.assertEqual(project.name, 'A study')
            for name in [' ', 'a' * 101]:
                with self.assertRaises(p.ProjectError):
                    p.rename_project(project, name)
            with self.assertRaisesRegex(p.ProjectError, 'Invalid project identifier'):
                p.get_project('../escape', root=root)
            with self.assertRaisesRegex(p.ProjectError, 'Project not found'):
                p.get_project('f' * 16, root=root)
            metadata = project.path / '.auto_zcurve/project.json'
            metadata.write_text('broken')
            with self.assertRaisesRegex(p.ProjectError, 'metadata could not be read'):
                p.get_project(project.project_id, root=root)
            (root / 'not-a-project').mkdir()
            (root / ('e' * 16)).touch()
            self.assertEqual(p.list_projects(root=root), [])
            self.assertEqual(p.list_projects(root=root / 'absent'), [])
            metadata.write_text('{}')
            self.assertEqual(p.get_project(project.project_id, root=root).name, 'Untitled project')
            metadata.unlink()
            with self.assertRaisesRegex(p.ProjectError, 'Project not found'):
                p.delete_project(project)

    def test_project_symlink_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'projects'
            root.mkdir()
            outside = Path(tmp) / 'outside'
            outside.mkdir()
            (root / ('a' * 16)).symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(p.ProjectError, 'Invalid project path'):
                p.get_project('a' * 16, root=root)

    def test_upload_names_collisions_and_invalid_files(self):
        self.assertEqual(p.safe_upload_name(r'C:\folder\a.pdf'), 'a.pdf')
        self.assertEqual(len(p.safe_upload_name('a' * 200 + '.pdf')), 164)
        for filename in ['...', ' ', 'file.txt']:
            with self.assertRaises(p.ProjectError):
                p.safe_upload_name(filename)
        with tempfile.TemporaryDirectory() as tmp:
            project = p.create_project('Test', root=Path(tmp))
            sources = project.path / 'sources'
            for name in ['study.pdf', 'study (2).pdf']:
                (sources / name).touch()
            self.assertEqual(p.unique_destination(sources, 'study.pdf').name, 'study (3).pdf')
            for data in [b'', b'%P', b'NOPE invalid']:
                with self.assertRaises(p.ProjectError):
                    p.save_upload(project, 'invalid.pdf', io.BytesIO(data))
                self.assertFalse((sources / 'invalid.pdf.uploading').exists())
                self.assertFalse((sources / 'invalid.pdf').exists())
            with self.assertRaises(p.UploadTooLarge):
                p.save_upload(project, 'large.pdf', io.BytesIO(b'%PDF larger'), max_bytes=4)
            self.assertFalse((sources / 'large.pdf.uploading').exists())

    def test_instruction_fallback_and_update_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = p.create_project('Test', root=Path(tmp))
            for value in [' ', 'a' * (p.MAX_INSTRUCTIONS_CHARS + 1)]:
                with self.assertRaises(p.ProjectError):
                    p.update_project_instructions(project, value)
            for value in [' ', 'a' * (p.MAX_SCHEMA_CHARS + 1), 'effects: []']:
                with self.assertRaises(p.ProjectError):
                    p.update_project_schema(project, value)
            self.assertEqual(p.update_project_instructions(project, p.read_project_instructions(project)), {'changed': False, 'reset': False})
            self.assertEqual(p.update_project_schema(project, p.read_project_schema(project)), {'changed': False, 'reset': False})
            self.assertEqual(p.update_project_instructions(project, 'New\r\ntext\r'), {'changed': True, 'reset': False})
            self.assertEqual(p.read_project_instructions(project), 'New\ntext\n')
            self.assertEqual(p.update_project_schema(project, 'effects: {claim: {type: string}}'), {'changed': True, 'reset': False})
            (project.path / p.PROJECT_INSTRUCTIONS_NAME).unlink()
            self.assertEqual(p.project_instruction_path(project.path), p.DEFAULT_INSTRUCTIONS)
            import shutil
            shutil.rmtree(project.path / 'output')
            self.assertFalse(p.project_has_analysis_results(project))
            p.reset_project_analysis(project)
            self.assertTrue((project.path / 'output/raw').is_dir())

    def test_snapshot_response_paths_are_bounded_and_fall_back_to_saved_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = p.create_project('Test', root=root / 'projects')
            (root / 'secret.txt').write_text('not allowed')
            raw = project.path / 'output/response.txt'
            raw.write_text('response')
            invalid = project.path / 'output/invalid.txt'
            invalid.write_bytes(b'\xff')
            for name in ['a', 'b', 'c', 'd']:
                (project.path / 'sources' / (name + '.pdf')).write_bytes(b'%PDF')
            save_extractions(project.path, [
                {'source_name': 'a.pdf', 'raw_response_path': str(root / 'secret.txt'), 'raw_json': 'fallback'},
                {'source_name': 'b.pdf', 'raw_response_path': 'output/response.txt', 'repaired_response_path': 'output/response.txt'},
                {'source_name': 'c.pdf', 'raw_response_path': 'output/invalid.txt'},
                {'source_name': 'd.pdf', 'raw_response_path': 'output/missing.txt'},
            ])
            articles = p.project_snapshot(project)['articles']
            self.assertEqual([article['raw_response'] for article in articles], ['fallback', 'response', '', ''])
            self.assertEqual(articles[1]['repaired_response'], 'response')

    def test_non_object_metadata_is_rejected_and_skipped_in_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = p.create_project('Test', root=root)
            metadata = project.path / '.auto_zcurve/project.json'
            for content in ['[]', 'null', '42', '"text"']:
                metadata.write_text(content)
                with self.subTest(content=content), self.assertRaises(p.ProjectError):
                    p.get_project(project.project_id, root=root)
                self.assertEqual(p.list_projects(root=root), [])
