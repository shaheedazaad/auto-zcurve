from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from auto_zcurve import preflight


class DependencyDetectionTests(unittest.TestCase):
    def test_python_dependency_inventory(self):
        with patch.object(preflight.importlib.util, 'find_spec', side_effect=lambda name: None if name in {'yaml', 'rich'} else object()):
            self.assertEqual(preflight.missing_python_dependencies(), ['PyYAML', 'rich'])

    def test_missing_parent_package_is_reported_as_missing_dependency(self):
        def lookup(name):
            if name == 'google.genai':
                raise ModuleNotFoundError("No module named 'google'")
            return object()
        with patch.object(preflight.importlib.util, 'find_spec', side_effect=lookup):
            self.assertEqual(preflight.missing_python_dependencies(), ['google-genai'])

    def test_system_tools_allow_rscript_in_place_of_r(self):
        for available, expected in [(set(), ['R', 'Quarto']), ({'R'}, ['Quarto']), ({'Rscript', 'quarto'}, []), ({'R', 'quarto'}, [])]:
            with self.subTest(available=available), patch.object(preflight.shutil, 'which', side_effect=lambda name: name if name in available else None):
                self.assertEqual(preflight.check_system_tools(), expected)

    def test_rscript_absence_does_not_spawn_process(self):
        with patch.object(preflight.shutil, 'which', return_value=None), patch.object(preflight.subprocess, 'run') as run:
            self.assertEqual(preflight.check_r_packages(), ['Rscript'])
            run.assert_not_called()

    def test_r_preflight_results_and_managed_library(self):
        cases = [(0, '', []), (1, '{"missing":["zcurve"]}', ['zcurve']), (1, '', []), (1, 'broken', ['unknown R package preflight failure'])]
        for platform in ['win32', 'darwin']:
            for code, stdout, expected in cases:
                with self.subTest(platform=platform, stdout=stdout), patch.object(preflight.shutil, 'which', return_value='Rscript'), patch.object(preflight.sys, 'platform', platform), patch.dict(os.environ, {'CONDA_PREFIX': '/managed'}, clear=True), patch.object(preflight.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, stdout)) as run:
                    self.assertEqual(preflight.check_r_packages(), expected)
                    kwargs = run.call_args.kwargs
                    library = str(Path('/managed') / ('Lib' if platform == 'win32' else 'lib') / 'R/library')
                    self.assertEqual(kwargs['env']['R_LIBS_USER'], library)
                    self.assertEqual(kwargs['env']['R_LIBS_SITE'], library)
                    self.assertFalse(kwargs['check'])
        with patch.object(preflight.shutil, 'which', return_value='Rscript'), patch.dict(os.environ, {}, clear=True), patch.object(preflight.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '')) as run:
            self.assertEqual(preflight.check_r_packages(), [])
            self.assertNotIn('R_LIBS_USER', run.call_args.kwargs['env'])


class DependencyInstallationTests(unittest.TestCase):
    def setUp(self):
        self.project = Path('/project with spaces')
        self.console = Mock()

    def test_python_ready_does_not_prompt_or_install(self):
        with patch.object(preflight, 'missing_python_dependencies', return_value=[]), patch.object(preflight, '_prompt_confirm') as prompt, patch.object(preflight.subprocess, 'run') as run:
            preflight.ensure_python_deps(self.project, True, self.console)
            prompt.assert_not_called()
            run.assert_not_called()

    def test_python_missing_noninteractive_or_declined(self):
        for interactive, message in [(False, 'Missing Python packages: rich'), (True, 'was not completed')]:
            with self.subTest(interactive=interactive), patch.object(preflight, 'missing_python_dependencies', return_value=['rich']), patch.object(preflight, '_prompt_confirm', return_value=False) as prompt, patch.object(preflight.subprocess, 'run') as run:
                with self.assertRaisesRegex(preflight.PreflightError, message):
                    preflight.ensure_python_deps(self.project, interactive, self.console)
                self.assertEqual(prompt.called, interactive)
                run.assert_not_called()

    def test_python_install_uses_platform_specific_project_venv(self):
        for platform, suffix in [('win32', 'Scripts/pip.exe'), ('linux', 'bin/pip')]:
            with self.subTest(platform=platform), patch.object(preflight, 'missing_python_dependencies', return_value=['rich']), patch.object(preflight, '_prompt_confirm', return_value=True), patch.object(preflight.sys, 'platform', platform), patch.object(preflight.subprocess, 'run') as run:
                with self.assertRaisesRegex(preflight.PreflightError, 'Virtualenv is ready'):
                    preflight.ensure_python_deps(self.project, True, self.console)
                self.assertEqual(run.call_args_list[0].args[0], [preflight.sys.executable, '-m', 'venv', str(self.project / '.venv')])
                self.assertEqual(run.call_args_list[1].args[0], [str(self.project / '.venv' / suffix), 'install', '-e', str(preflight.REPO_ROOT)])
                self.assertTrue(all(call.kwargs['check'] for call in run.call_args_list))

    def test_r_tools_missing_stop_before_package_check(self):
        with patch.object(preflight, 'check_system_tools', return_value=['Quarto']), patch.object(preflight, 'check_r_packages') as packages:
            with self.assertRaisesRegex(preflight.PreflightError, 'Missing system tools: Quarto'):
                preflight.ensure_r_deps(self.project, True, self.console)
            packages.assert_not_called()

    def test_r_ready_does_not_prompt(self):
        with patch.object(preflight, 'check_system_tools', return_value=[]), patch.object(preflight, 'check_r_packages', return_value=[]), patch.object(preflight, '_prompt_confirm') as prompt:
            preflight.ensure_r_deps(self.project, True, self.console)
            prompt.assert_not_called()

    def test_r_missing_noninteractive_declined_and_accepted(self):
        for interactive, consent in [(False, False), (True, False), (True, True)]:
            with self.subTest(interactive=interactive, consent=consent), patch.object(preflight, 'check_system_tools', return_value=[]), patch.object(preflight, 'check_r_packages', return_value=['zcurve', 'dplyr']), patch.object(preflight, '_prompt_confirm', return_value=consent) as prompt, patch.object(preflight.subprocess, 'run') as run:
                if consent:
                    preflight.ensure_r_deps(self.project, interactive, self.console)
                    self.assertEqual(run.call_args.args[0][-3:], [str(self.project), 'zcurve', 'dplyr'])
                    self.assertTrue(run.call_args.kwargs['check'])
                else:
                    with self.assertRaises(preflight.PreflightError):
                        preflight.ensure_r_deps(self.project, interactive, self.console)
                    run.assert_not_called()
                self.assertEqual(prompt.called, interactive)

    def test_preflight_checks_python_before_r(self):
        calls = Mock()
        with patch.object(preflight, 'ensure_python_deps', calls.python), patch.object(preflight, 'ensure_r_deps', calls.r):
            preflight.run_preflight(self.project, False, self.console)
            self.assertEqual([call[0] for call in calls.mock_calls], ['python', 'r'])
            calls.python.assert_called_once_with(self.project, False, self.console)
            calls.r.assert_called_once_with(self.project, False, self.console)

    def test_confirmation_widget_and_input_fallback(self):
        with patch('questionary.confirm') as confirm:
            confirm.return_value.ask.return_value = True
            self.assertTrue(preflight._prompt_confirm('Proceed?', default=True))
            confirm.assert_called_once_with('Proceed?', default=True)
        for answer, default, expected in [('', False, False), ('', True, True), (' YES ', False, True), ('y', False, True), ('no', True, False)]:
            with self.subTest(answer=answer, default=default), patch('questionary.confirm', side_effect=RuntimeError('terminal unavailable')), patch('builtins.input', return_value=answer):
                self.assertEqual(preflight._prompt_confirm('Proceed?', default), expected)
