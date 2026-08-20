from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from auto_zcurve.report import render_report


class ReportRenderTests(unittest.TestCase):
    def test_render_report_runs_quarto_from_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            schema = project / "extraction_schema.yml"
            schema.write_text("name: test\n", encoding="utf-8")
            calls = []

            def fake_run(args, **kwargs):
                calls.append((args, kwargs))
                cwd = Path(kwargs["cwd"])
                (cwd / "report.html").write_text("<html></html>", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with (
                patch("auto_zcurve.report.shutil.which", return_value="/usr/bin/quarto"),
                patch("auto_zcurve.report._current_r_libs", return_value=None),
                patch("auto_zcurve.report.subprocess.run", side_effect=fake_run),
            ):
                report_path = render_report(
                    project_dir=project,
                    schema_path=schema,
                    model_name="gemini-3.1-pro-preview",
                    effect_definition=None,
                )

            self.assertEqual(report_path, project / "output" / "report.html")
            self.assertTrue(report_path.exists())
            self.assertTrue((project / "output" / "report.qmd").exists())
            reproducible_qmd = (project / "output" / "report.qmd").read_text(
                encoding="utf-8"
            )
            self.assertIn('read_csv("disclosure_table.csv"', reproducible_qmd)
            self.assertIn("zcurve_clustered(", reproducible_qmd)
            self.assertIn("is.finite(analysis_z)", reproducible_qmd)
            report_template = Path("report/report_template.qmd").read_text(encoding="utf-8")
            self.assertIn("run_date", report_template)
            self.assertIn("app_version", report_template)
            self.assertIn("truncate_csv_text", report_template)
            self.assertFalse((project / "report.html").exists())
            self.assertEqual(Path(calls[0][1]["cwd"]), project / "output")
            self.assertNotIn("--output-dir", calls[0][0])
            self.assertIn("report.qmd", calls[0][0])
            self.assertEqual(calls[0][1]["env"]["AUTO_ZCURVE_MODEL_NAME"], "gemini-3.1-pro-preview")
            self.assertIn("AUTO_ZCURVE_VERSION", calls[0][1]["env"])


if __name__ == "__main__":
    unittest.main()
