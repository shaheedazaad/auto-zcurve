from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from rich.console import Console

from auto_zcurve import console


class ConsoleTests(unittest.TestCase):
    def test_plain_messages_tables_and_progress(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(console, 'Console', None), redirect_stdout(out), redirect_stderr(err):
            cli = console.CliConsole()
            cli.print('first', 'second')
            cli.title('Title')
            cli.title('Title', 'Subtitle')
            cli.info('Information')
            cli.warn('Careful')
            cli.error('Failed')
            cli.success('Done')
            cli.table('Results', ['Name', 'Count'], [['study', 2]])
            with cli.progress(2, 'Extracting') as progress:
                progress.advance()
                progress.advance('Finished study')
        self.assertEqual(out.getvalue(), 'first second\nauto-zcurve: Title\nauto-zcurve: Title\nSubtitle\nInformation\nWarning: Careful\nError: Failed\nDone\nResults\nName | Count\nstudy | 2\nExtracting\n')
        self.assertEqual(err.getvalue(), 'Processed: 1/2\nFinished study: 2/2\n')

    def test_rich_output_renders_messages_and_table(self):
        out = io.StringIO()
        cli = console.CliConsole()
        cli.rich = Console(file=out, width=80, color_system=None)
        cli.title('Title')
        cli.title('Title', 'Subtitle')
        cli.info('Information')
        cli.warn('Careful')
        cli.error('Failed')
        cli.success('Done')
        cli.table('Results', ['Name', 'Count'], [['study', 2]])
        with cli.progress(2, 'Extracting') as progress:
            progress.advance()
            progress.advance('Finished study')
        rendered = out.getvalue()
        for expected in ['auto-zcurve', 'Title', 'Subtitle', 'Information', 'Careful', 'Failed', 'Done', 'Results', 'Name', 'Count', 'study', 'Finished study', '2/2']:
            self.assertIn(expected, rendered)
        self.assertNotIn('[cyan]', rendered)

    def test_rich_progress_adapter_preserves_description_when_unspecified(self):
        progress = Mock()
        adapter = console._RichProgressAdapter(progress, 42)
        adapter.advance()
        progress.update.assert_called_with(42, advance=1)
        adapter.advance('Next article')
        progress.update.assert_called_with(42, advance=1, description='Next article')
