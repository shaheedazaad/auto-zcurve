import builtins
import runpy
import sys
import unittest
from unittest.mock import Mock, patch

from auto_zcurve import cli


class CliEntryTests(unittest.TestCase):
    def test_explicit_web_options(self):
        with patch('auto_zcurve.web.launch_web', return_value=17) as launch:
            self.assertEqual(cli.main(['web', '--no-browser', '--port', '9000']), 17)
        launch.assert_called_once_with(open_browser=False, port=9000)

    def test_optional_prompt_library_absent(self):
        importing = builtins.__import__
        def import_module(name, *args, **kwargs):
            if name == 'questionary':
                raise ImportError('missing')
            return importing(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=import_module):
            self.assertIsNone(cli._questionary())

    def test_module_entry_returns_launch_exit_status(self):
        with patch.object(sys, 'argv', ['auto-zcurve', 'web']), patch('auto_zcurve.web.launch_web', return_value=7), self.assertRaises(SystemExit) as caught:
            runpy.run_module('auto_zcurve.cli', run_name='__main__')
        self.assertEqual(caught.exception.code, 7)

    def test_summary_without_zcurve_section(self):
        summary = Mock(report_path=None, successful_pdfs=1, failed_pdfs=0, extracted_effects=2, usable_zcurve_inputs=1, input_tokens=3, output_tokens=4, total_tokens=7)
        console = Mock()
        with patch.object(cli, 'format_run_result', return_value='Report: not rendered'):
            cli.print_summary(summary, console, cli.Path('.'))
        console.table.assert_called_once()
        console.print.assert_not_called()
