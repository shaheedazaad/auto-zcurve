from __future__ import annotations

import unittest
from unittest.mock import patch

from auto_zcurve.cli import build_parser, main


class CliWebTests(unittest.TestCase):
    def test_web_and_run_commands_are_available(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args(["web", "--no-browser"]).command, "web")
        parsed = parser.parse_args(["run", "/tmp/project", "--extractor", "Lab Gemini"])
        self.assertEqual(parsed.extractor, "Lab Gemini")
        self.assertEqual(parser.parse_args(["extractors"]).command, "extractors")
        for removed in ["--provider", "--model", "--api-key", "--parallel"]:
            with self.subTest(flag=removed), patch("sys.stderr"), self.assertRaises(SystemExit):
                parser.parse_args(["run", "/tmp/project", removed, "x"])

    def test_terminal_interface_commands_are_rejected(self):
        parser = build_parser()
        self.assertNotIn("tui", parser.format_help())
        self.assertNotIn("gui", parser.format_help())
        for command in ["tui", "gui"]:
            with self.subTest(command=command), patch("sys.stderr"), self.assertRaises(SystemExit) as raised:
                parser.parse_args([command])
            self.assertEqual(raised.exception.code, 2)

    @patch("auto_zcurve.web.launch_web", return_value=0)
    def test_no_argument_command_launches_browser_app(self, launch):
        self.assertEqual(main([]), 0)
        launch.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
