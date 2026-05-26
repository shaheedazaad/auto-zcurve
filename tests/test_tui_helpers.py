import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from auto_zcurve.tui import (
    article_summary,
    display_path,
    is_hidden_path,
    request_terminal_resize,
    requested_terminal_size,
    terminal_resize_sequence,
)
from auto_zcurve.runner import RunSummary
from auto_zcurve.user_facing import (
    check_project_readiness,
    classify_error,
    format_run_result,
    open_report_path,
    report_opener_command,
)


def no_missing_deps():
    return [], [], []


class TuiHelperTests(unittest.TestCase):
    def test_article_summary_counts_pdfs_in_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            sources = project / "sources"
            nested = sources / "nested"
            nested.mkdir(parents=True)
            (sources / "a.pdf").write_bytes(b"")
            (nested / "b.pdf").write_bytes(b"")
            (sources / "ignore.txt").write_text("x", encoding="utf-8")

            count, message = article_summary(project)

            self.assertEqual(count, 2)
            self.assertIn("2 PDF articles", message)

    def test_article_summary_reports_missing_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            count, message = article_summary(Path(tmp))

            self.assertEqual(count, 0)
            self.assertIn("already contains sources/", message)

    def test_hidden_path_detection_for_file_picker(self):
        self.assertTrue(is_hidden_path(".git"))
        self.assertTrue(is_hidden_path(Path("/tmp/.env")))
        self.assertFalse(is_hidden_path("project"))
        self.assertFalse(is_hidden_path(Path("/tmp/project")))

    def test_requested_terminal_size_returns_none_when_large_enough(self):
        self.assertIsNone(requested_terminal_size(140, 52, min_columns=124, min_rows=48))

    def test_requested_terminal_size_expands_only_needed_dimensions(self):
        self.assertEqual(requested_terminal_size(100, 52, min_columns=124, min_rows=48), (124, 52))
        self.assertEqual(requested_terminal_size(140, 32, min_columns=124, min_rows=48), (140, 48))
        self.assertEqual(requested_terminal_size(100, 32, min_columns=124, min_rows=48), (124, 48))

    def test_terminal_resize_sequence_uses_xterm_window_control(self):
        self.assertEqual(terminal_resize_sequence(124, 48), "\033[8;48;124t")

    def test_request_terminal_resize_writes_resize_sequence(self):
        class FakeStream:
            def __init__(self):
                self.buffer = ""

            def isatty(self):
                return True

            def fileno(self):
                raise OSError("no fd")

            def write(self, value):
                self.buffer += value

            def flush(self):
                pass

        stream = FakeStream()
        with patch("auto_zcurve.tui.shutil.get_terminal_size", return_value=os.terminal_size((80, 24))):
            resized = request_terminal_resize(min_columns=124, min_rows=48, stream=stream)

        self.assertTrue(resized)
        self.assertEqual(stream.buffer, "\033[8;48;124t")

    def test_display_path_prefers_base_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            report = project / "report" / "report.html"

            self.assertEqual(display_path(report, base=project), "report/report.html")

    def test_display_path_falls_back_to_cwd_relative_path(self):
        previous_cwd = Path.cwd()
        self.addCleanup(os.chdir, previous_cwd)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "example" / "sources"
            nested.mkdir(parents=True)
            os.chdir(root)

            self.assertEqual(display_path(nested), "example/sources")

    @patch("auto_zcurve.user_facing.load_saved_api_key", return_value=None)
    def test_readiness_reports_missing_sources(self, _saved_key):
        with tempfile.TemporaryDirectory() as tmp:
            readiness = check_project_readiness(
                Path(tmp),
                api_key="key",
                model="gemini-2.5-flash",
                dependency_check=no_missing_deps,
            )

            self.assertFalse(readiness.ready)
            self.assertEqual(readiness.issues[0].key, "sources_missing")
            self.assertIn("already contains sources/", readiness.next_action)

    @patch("auto_zcurve.user_facing.load_saved_api_key", return_value=None)
    def test_readiness_reports_empty_sources(self, _saved_key):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "sources").mkdir()

            readiness = check_project_readiness(
                project,
                api_key="key",
                model="gemini-2.5-flash",
                dependency_check=no_missing_deps,
            )

            self.assertFalse(readiness.ready)
            self.assertEqual(readiness.issues[0].key, "sources_empty")
            self.assertIn("Add PDFs", readiness.next_action)

    @patch("auto_zcurve.user_facing.load_saved_api_key", return_value=None)
    def test_readiness_allows_missing_schema_when_default_exists(self, _saved_key):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            sources = project / "sources"
            sources.mkdir()
            (sources / "article.pdf").write_bytes(b"%PDF")

            readiness = check_project_readiness(
                project,
                api_key="key",
                model="gemini-2.5-flash",
                dependency_check=no_missing_deps,
            )

            self.assertTrue(readiness.ready)
            self.assertEqual(readiness.next_action, "Ready to run extraction.")

    @patch("auto_zcurve.user_facing.load_saved_api_key", return_value=None)
    def test_readiness_reports_missing_schema_when_default_unavailable(self, _saved_key):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            sources = project / "sources"
            sources.mkdir()
            (sources / "article.pdf").write_bytes(b"%PDF")
            with patch("auto_zcurve.user_facing.DEFAULT_SCHEMA", project / "missing.yml"):
                readiness = check_project_readiness(
                    project,
                    api_key="key",
                    model="gemini-2.5-flash",
                    dependency_check=no_missing_deps,
                )

            self.assertFalse(readiness.ready)
            self.assertEqual(readiness.issues[0].key, "schema_missing")

    @patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False)
    @patch("auto_zcurve.user_facing.load_dotenv")
    @patch("auto_zcurve.user_facing.load_saved_api_key", return_value=None)
    def test_readiness_reports_missing_api_key(self, _saved_key, _load_dotenv):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            sources = project / "sources"
            sources.mkdir()
            (sources / "article.pdf").write_bytes(b"%PDF")

            readiness = check_project_readiness(
                project,
                api_key="",
                model="gemini-2.5-flash",
                dependency_check=no_missing_deps,
            )

            self.assertFalse(readiness.ready)
            self.assertIn("API key", readiness.issues[0].title)

    @patch("auto_zcurve.user_facing.load_saved_api_key", return_value=None)
    def test_readiness_ready_project(self, _saved_key):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            sources = project / "sources"
            sources.mkdir()
            (project / "extraction_schema.yml").write_text("effects: {}\n", encoding="utf-8")
            (sources / "article.pdf").write_bytes(b"%PDF")

            readiness = check_project_readiness(
                project,
                api_key="key",
                model="gemini-2.5-flash",
                dependency_check=no_missing_deps,
            )

            self.assertTrue(readiness.ready)
            self.assertEqual(readiness.pdf_count, 1)

    def test_error_classification_for_common_messages(self):
        self.assertEqual(classify_error("API key not valid").title, "Gemini rejected the API key")
        self.assertEqual(classify_error("File exceeds max_upload_size_mb (20 MB).").title, "PDF too large")
        self.assertEqual(classify_error("Quarto report rendering failed.").title, "Report could not be created")
        self.assertEqual(classify_error("Missing system tools: R, Quarto").title, "Quarto missing")
        self.assertEqual(classify_error("Missing R packages: renv").title, "Missing dependency")
        self.assertEqual(classify_error("The extraction schema must define at least one effect field.").title, "Extraction schema problem")
        self.assertEqual(classify_error("No effect rows contain usable values").title, "No usable statistics found")

    def test_report_opener_command_selection(self):
        self.assertEqual(report_opener_command(Path("report.html"), "Darwin"), ["open", "report.html"])
        self.assertEqual(report_opener_command(Path("report.html"), "Linux"), ["xdg-open", "report.html"])
        self.assertIsNone(report_opener_command(Path("report.html"), "Windows"))

    def test_open_report_path_uses_platform_openers(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.html"
            report.write_text("<html></html>", encoding="utf-8")
            popen = Mock()
            startfile = Mock()

            open_report_path(report, system="Darwin", popen=popen)
            self.assertEqual(popen.call_args.args[0][0], "open")

            open_report_path(report, system="Linux", popen=popen)
            self.assertEqual(popen.call_args.args[0][0], "xdg-open")

            open_report_path(report, system="Windows", startfile=startfile)
            startfile.assert_called_once_with(str(report.resolve()))

    def test_format_run_result_uses_project_relative_report_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            summary = RunSummary(
                report_path=project / "output" / "report.html",
                successful_pdfs=2,
                failed_pdfs=1,
                extracted_effects=12,
                usable_zcurve_inputs=9,
                input_tokens=1000,
                output_tokens=200,
                total_tokens=1200,
            )

            result = format_run_result(summary, project)

            self.assertIn("Report: output/report.html", result)
            self.assertIn("Failed PDFs: 1", result)
            self.assertIn("Input tokens: 1000", result)

    def test_format_run_result_keeps_zcurve_summary_out_of_left_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            output = project / "output"
            output.mkdir()
            (output / "zcurve_summary.txt").write_text("EDR 0.310\nODR 0.890\n", encoding="utf-8")
            summary = RunSummary(
                report_path=output / "report.html",
                successful_pdfs=2,
                failed_pdfs=0,
                extracted_effects=12,
                usable_zcurve_inputs=9,
                input_tokens=1000,
                output_tokens=200,
                total_tokens=1200,
            )

            result = format_run_result(summary, project)

            self.assertNotIn("Z-Curve Summary:", result)
            self.assertNotIn("EDR 0.310", result)


if __name__ == "__main__":
    unittest.main()
