from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_zcurve import report


class ReportEnvironmentTests(unittest.TestCase):
    def test_missing_rscript(self):
        with patch.object(report.shutil, 'which', return_value=None), patch.object(report.subprocess, 'run') as run:
            self.assertIsNone(report._current_r_libs())
            run.assert_not_called()

    def test_library_discovery_failures_and_platforms(self):
        for platform in ['win32', 'linux']:
            for prefix in ['', '/managed']:
                for code, stdout, expected in [(0, ' /r/lib1:/r/lib2 \n', '/r/lib1:/r/lib2'), (0, ' ', None), (1, 'error', None)]:
                    with self.subTest(platform=platform, prefix=prefix, code=code, stdout=stdout), patch.object(report.shutil, 'which', return_value='/bin/Rscript'), patch.object(report.sys, 'platform', platform), patch.dict(os.environ, {'CONDA_PREFIX': prefix}, clear=True), patch.object(report.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, stdout)) as run:
                        self.assertEqual(report._current_r_libs(), expected)
                        env = run.call_args.kwargs['env']
                        if prefix:
                            expected_lib = str(Path(prefix) / ('Lib' if platform == 'win32' else 'lib') / 'R/library')
                            self.assertEqual(env['R_LIBS_USER'], expected_lib)
                            self.assertEqual(env['R_LIBS_SITE'], expected_lib)
                        else:
                            self.assertNotIn('R_LIBS_USER', env)
                        self.assertEqual(run.call_args.args[0][0], '/bin/Rscript')


class ReportFailureTests(unittest.TestCase):
    def test_quarto_missing_writes_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(report.shutil, 'which', return_value=None):
            project = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, 'Quarto is not installed'):
                report.render_report(project_dir=project, schema_path=project / 'schema.yml', model_name='model')
            log = (project / 'output/report_render.log').read_text()
            self.assertIn('status: not_started', log)
            self.assertIn('Quarto is not installed', log)

    def test_failed_launch_exit_and_missing_html_do_not_return_stale_report(self):
        for outcome, message, status in [(OSError('cannot execute'), 'Could not launch Quarto', 'launch_failed'), (subprocess.CompletedProcess([], 1, 'stdout detail', 'stderr detail'), 'rendering failed', 'failed'), (subprocess.CompletedProcess([], 0, '', ''), 'did not create', 'failed')]:
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp)
                output = project / 'output'
                output.mkdir()
                (output / 'report.html').write_text('stale report')
                with patch.object(report.shutil, 'which', side_effect=lambda tool: '/bin/quarto' if tool == 'quarto' else None), patch.object(report, '_current_r_libs', return_value=None), patch.object(report.subprocess, 'run', **({'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome})):
                    with self.assertRaisesRegex(RuntimeError, message):
                        report.render_report(project_dir=project, schema_path=project / 'schema.yml', model_name='model')
                self.assertFalse((output / 'report.html').exists())
                log = (output / 'report_render.log').read_text()
                self.assertIn('status: ' + status, log)
                if isinstance(outcome, OSError):
                    self.assertIn('cannot execute', log)
                else:
                    self.assertIn('html_created: False', log)
                    self.assertIn(outcome.stdout or '(empty)', log)
                    self.assertIn(outcome.stderr or '(empty)', log)

    def test_success_passes_resolved_paths_and_r_libraries(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            instructions = project / 'custom.md'
            schema = project / 'custom.yml'
            def render(command, **kwargs):
                (Path(kwargs['cwd']) / 'report.html').write_text('<html>report</html>')
                return subprocess.CompletedProcess(command, 0, 'rendered', '')
            with patch.object(report.shutil, 'which', side_effect=lambda tool: '/tools/' + tool), patch.object(report, '_current_r_libs', return_value='/libraries'), patch.object(report.subprocess, 'run', side_effect=render) as run:
                path = report.render_report(project_dir=project, schema_path=schema, model_name='custom-model', instruction_path=instructions)
            env = run.call_args.kwargs['env']
            for key, value in {'AUTO_ZCURVE_PROJECT_DIR': project.resolve(), 'AUTO_ZCURVE_SCHEMA_PATH': schema.resolve(), 'AUTO_ZCURVE_INSTRUCTIONS_PATH': instructions.resolve(), 'AUTO_ZCURVE_OUTPUT_DIR': (project / 'output').resolve()}.items():
                self.assertEqual(env[key], str(value))
            self.assertEqual(env['QUARTO_R'], '/tools')
            self.assertEqual(env['R_LIBS'], '/libraries')
            self.assertEqual(env['AUTO_ZCURVE_MODEL_NAME'], 'custom-model')
            self.assertEqual(path.read_text(), '<html>report</html>')
            self.assertEqual((project / 'output/report.qmd').read_bytes(), report.REPRODUCIBLE_REPORT_TEMPLATE.read_bytes())
            self.assertIn('status: ok', (project / 'output/report_render.log').read_text())
