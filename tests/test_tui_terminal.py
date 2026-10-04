import builtins
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from auto_zcurve import tui


class TerminalFallbackTests(unittest.TestCase):
    def test_paths_outside_working_directory_and_under_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with patch.object(Path, 'cwd', return_value=root / 'work'), patch.object(Path, 'home', return_value=root / 'home'):
                self.assertEqual(tui.display_path(root / 'home/file', base=root / 'other'), '~/file')
                self.assertEqual(tui.display_path(root / 'elsewhere'), str(root / 'elsewhere'))
                self.assertEqual(tui.article_summary(root / 'missing')[0], 0)

    def test_resize_opt_out_and_tty_open_failure(self):
        with patch.dict(os.environ, {tui.TERMINAL_RESIZE_OPT_OUT: '1'}):
            self.assertFalse(tui.request_terminal_resize())
        stream = Mock()
        stream.isatty.return_value = False
        with patch.dict(os.environ, {}, clear=True), patch.object(tui.sys, 'stdout', stream), patch.object(tui.os, 'name', 'posix'), patch('builtins.open', side_effect=OSError('no tty')):
            self.assertFalse(tui.request_terminal_resize())

    def test_owned_tty_is_closed_for_every_outcome(self):
        for is_tty, size, resized in [(False, (10, 10), False), (True, (10, 10), True), (True, (200, 100), False)]:
            with self.subTest(is_tty=is_tty, size=size):
                stdout = Mock()
                stdout.isatty.return_value = False
                tty = Mock()
                tty.isatty.return_value = is_tty
                with patch.dict(os.environ, {}, clear=True), patch.object(tui.sys, 'stdout', stdout), patch.object(tui.os, 'name', 'posix'), patch('builtins.open', return_value=tty), patch.object(tui.os, 'get_terminal_size', return_value=os.terminal_size(size)), patch.object(tui.time, 'sleep'):
                    self.assertEqual(tui.request_terminal_resize(), resized)
                tty.close.assert_called_once()

    def test_missing_textual_has_actionable_error(self):
        importing = builtins.__import__
        def import_module(name, *args, **kwargs):
            if name == 'textual.app':
                raise ImportError('missing')
            return importing(name, *args, **kwargs)
        with patch.object(tui, 'request_terminal_resize'), patch('builtins.__import__', side_effect=import_module), self.assertRaisesRegex(RuntimeError, 'Textual is not installed'):
            tui.run_tui()

    def test_large_borrowed_terminal_is_not_closed(self):
        stdout = Mock()
        stdout.isatty.return_value = True
        with patch.dict(os.environ, {}, clear=True), patch.object(tui.sys, 'stdout', stdout), patch.object(tui.os, 'get_terminal_size', return_value=os.terminal_size((200, 100))):
            self.assertFalse(tui.request_terminal_resize())
        stdout.close.assert_not_called()
        stdout.write.assert_not_called()
