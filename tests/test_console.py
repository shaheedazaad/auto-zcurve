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

    def test_secrets_are_redacted_from_everything_printed(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(console, 'Console', None), redirect_stdout(out), redirect_stderr(err):
            cli = console.CliConsole(['secret-key'])
            cli.print('a secret-key')
            cli.title('secret-key', 'secret-key')
            cli.error('secret-key')
            cli.table('secret-key', ['secret-key'], [['secret-key']])
            with cli.progress(1, 'secret-key') as progress:
                progress.advance('secret-key')
        self.assertNotIn('secret-key', out.getvalue() + err.getvalue())
        self.assertIn('Error: [redacted]', out.getvalue())

        out = io.StringIO()
        cli = console.CliConsole()
        cli.secrets.append('secret-key')
        cli.rich = Console(file=out, width=80, color_system=None)
        cli.title('secret-key', 'secret-key')
        cli.error('failed secret-key')
        cli.table('secret-key', ['secret-key'], [['secret-key']])
        with cli.progress(1, 'secret-key') as progress:
            progress.advance('secret-key')
        self.assertNotIn('secret-key', out.getvalue())
        self.assertIn('failed [redacted]', out.getvalue())

    def test_secrets_inside_objects_and_renderables_are_redacted(self):
        from rich.panel import Panel
        from rich.table import Table

        class Carrier:
            def __str__(self):
                return 'key=secret-key'

        out = io.StringIO()
        with patch.object(console, 'Console', None), redirect_stdout(out):
            console.CliConsole(['secret-key']).print(Carrier(), 7)
        self.assertEqual(out.getvalue(), 'key=[redacted] 7\n')

        table = Table(title='secret-key')
        table.add_column('Key')
        table.add_row('secret-key')
        for part in (Carrier(), Panel('secret-key'), table):
            with self.subTest(part=type(part).__name__):
                out = io.StringIO()
                cli = console.CliConsole(['secret-key'])
                cli.rich = Console(file=out, width=80, color_system=None)
                cli.print(part)
                self.assertNotIn('secret-key', out.getvalue())
                self.assertIn('[redacted]', out.getvalue())

        # Without secrets a renderable is printed as is, keeping its styles.
        out = io.StringIO()
        cli = console.CliConsole()
        cli.rich = Console(file=out, width=80, color_system=None)
        cli.print(Panel('plain'))
        self.assertIn('plain', out.getvalue())
