from __future__ import annotations

import unittest
from unittest.mock import patch

from auto_zcurve.cli import build_parser, main


class CliWebTests(unittest.TestCase):
    def test_web_and_tui_commands_are_available(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args(["web", "--no-browser"]).command, "web")
        self.assertEqual(parser.parse_args(["tui"]).command, "tui")

    @patch("auto_zcurve.web.launch_web", return_value=0)
    def test_no_argument_command_launches_browser_app(self, launch):
        self.assertEqual(main([]), 0)
        launch.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
