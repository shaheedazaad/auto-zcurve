import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from auto_zcurve import user_facing as u
from auto_zcurve.runner import RunSummary


class ErrorPresentationTests(unittest.TestCase):
    def test_error_categories_preserve_detail_and_give_next_action(self):
        cases = [('OPENROUTER_API_KEY required', 'OpenRouter API key missing'), ('OpenRouter API key missing', 'OpenRouter API key missing'), ('GEMINI_API_KEY required', 'Gemini API key missing'), ('OpenRouter 403', 'OpenRouter rejected the API key'), ('invalid api key', 'Gemini rejected the API key'), ('maximum size exceeded', 'PDF too large'), ('Missing system tools: Quarto', 'Quarto missing'), ('Missing R packages: zcurve', 'Missing dependency'), ('Quarto is not installed', 'Quarto missing'), ('report rendering failed', 'Report could not be created'), ('schema JSON required', 'Extraction schema problem'), ('OpenRouter request failed', 'OpenRouter request failed'), ('network timeout', 'Gemini request failed'), ('no usable statistics', 'No usable statistics found'), ('unexpected failure', 'Run failed')]
        for detail, title in cases:
            with self.subTest(detail=detail):
                error = u.classify_error(RuntimeError(detail))
                self.assertEqual(error.title, title)
                self.assertEqual(error.technical_detail, detail)
                self.assertTrue(error.explanation)
                self.assertTrue(error.next_action)
                self.assertEqual(error.compact(), f'{title}: {error.explanation} {error.next_action}')

    def test_result_paths_and_metrics(self):
        summary = SimpleNamespace(report_path=None, successful_pdfs=2, needs_review_pdfs=0, failed_pdfs=1, extracted_effects=4, usable_zcurve_inputs=3, input_tokens=10, output_tokens=5, total_tokens=15)
        for report, expected in [(None, 'not rendered'), (Path('/project/output/report.html'), 'output/report.html'), (Path('/elsewhere/report.html'), '/elsewhere/report.html')]:
            summary.report_path = report
            result = u.format_run_result(summary, Path('/project'))
            self.assertEqual(result.splitlines()[0], 'Report: ' + expected)
            self.assertIn('Successful PDFs: 2', result)
            self.assertIn('Total tokens: 15', result)

    def test_error_classification_for_common_messages(self):
        self.assertEqual(u.classify_error("API key not valid").title, "Gemini rejected the API key")
        self.assertEqual(u.classify_error("File exceeds max_upload_size_mb (20 MB).").title, "PDF too large")
        self.assertEqual(u.classify_error("Quarto report rendering failed.").title, "Report could not be created")
        self.assertEqual(u.classify_error("Missing system tools: R, Quarto").title, "Quarto missing")
        self.assertEqual(u.classify_error("Missing R packages: renv").title, "Missing dependency")
        self.assertEqual(u.classify_error("The extraction schema must define at least one effect field.").title, "Extraction schema problem")
        self.assertEqual(u.classify_error("No effect rows contain usable values").title, "No usable statistics found")

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

            result = u.format_run_result(summary, project)

            self.assertIn("Report: output/report.html", result)
            self.assertIn("Failed PDFs: 1", result)
            self.assertIn("Input tokens: 1000", result)

    def test_format_run_result_omits_zcurve_summary(self):
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

            result = u.format_run_result(summary, project)

            self.assertNotIn("Z-Curve Summary:", result)
            self.assertNotIn("EDR 0.310", result)
