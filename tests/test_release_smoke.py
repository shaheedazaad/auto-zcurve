from __future__ import annotations

import csv
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_zcurve.config import RunSettings
from auto_zcurve.console import CliConsole
from auto_zcurve.gemini import ExtractionResult
from auto_zcurve.report import _current_r_libs
from auto_zcurve.runner import run_project


@unittest.skipUnless(
    os.environ.get("AUTO_ZCURVE_RELEASE_SMOKE") == "1",
    "Run with `pixi run release-smoke` inside the locked environment.",
)
class ReleaseSmokeTests(unittest.TestCase):
    def test_fixture_runs_through_real_r_and_quarto_report(self):
        with tempfile.TemporaryDirectory(prefix="auto zcurve ünicode ") as tmp:
            project = Path(tmp) / "project with spaces"
            sources = project / "sources"
            sources.mkdir(parents=True)
            pdf = sources / "small fixture.pdf"
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")
            effects = [
                {
                    "claim": f"Fixture claim {index}",
                    "reported_statistic": f"p={p_value}",
                    "significant": True,
                    "one_sided": False,
                }
                for index, p_value in enumerate(
                    [0.001, 0.002, 0.004, 0.006, 0.009, 0.012, 0.018, 0.023, 0.031, 0.041],
                    start=1,
                )
            ]
            effects.append(
                {
                    "claim": "Underflow fixture",
                    "reported_statistic": "chi(5151)=15536.35",
                    "significant": True,
                    "one_sided": False,
                }
            )
            effects.append(
                {
                    "claim": "Rounded boundary fixture",
                    "reported_statistic": "p=1",
                    "significant": False,
                    "one_sided": False,
                }
            )

            result = ExtractionResult(
                source_path=pdf,
                source_name=pdf.name,
                status="ok",
                model_used="fixture-model",
                data={"meta_data": {"doi": "10.0000/fixture"}, "effects": effects},
                raw_json="{}",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            )
            with patch("auto_zcurve.runner.extract_pdf", return_value=result):
                summary = run_project(
                    project_dir=project,
                    settings=RunSettings(primary_model="fixture-model", parallel_requests=1),
                    assume_yes=True,
                    interactive=False,
                    force=False,
                    skip_report=False,
                    console=CliConsole(),
                    api_key="not-used",
                )

            self.assertIsNotNone(summary)
            self.assertTrue((project / "output" / "report.html").is_file())
            self.assertGreater((project / "output" / "zcurve_plot.png").stat().st_size, 0)
            self.assertTrue((project / "output" / "disclosure_table.csv").is_file())
            self.assertTrue(
                (project / "output" / "zcurve_reproduction_settings.csv").is_file()
            )
            self.assertEqual(summary.successful_pdfs, 1)
            report = (project / "output" / "report.html").read_text(encoding="utf-8")
            self.assertIn("Z-curve input warning", report)
            with (project / "output" / "disclosure_table.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            excluded = next(
                row for row in rows if row["reported_statistic"] == "chi(5151)=15536.35"
            )
            self.assertEqual(excluded["usable_for_zcurve"], "FALSE")
            self.assertEqual(excluded["analysis_p"], "0")
            self.assertEqual(excluded["analysis_z"], "NA")
            self.assertIn("non-finite z-value", excluded["zcurve_exclusion_reason"])

            reproduction_cache = project / "output" / ".reproduction-cache"
            reproduction_home = reproduction_cache / "home"
            reproduction_home.mkdir(parents=True)
            reproduction_env = os.environ.copy()
            reproduction_env.update(
                {
                    "DENO_DIR": str(reproduction_cache / "deno"),
                    "QUARTO_CACHE": str(reproduction_cache / "quarto"),
                    "XDG_CACHE_HOME": str(reproduction_cache / "xdg"),
                    "HOME": str(reproduction_home),
                    "USERPROFILE": str(reproduction_home),
                }
            )
            reproduction_r_libs = _current_r_libs()
            reproduction_rscript = shutil.which("Rscript")
            if reproduction_rscript:
                reproduction_env["QUARTO_R"] = str(
                    Path(reproduction_rscript).resolve().parent
                )
            if reproduction_r_libs:
                reproduction_env["R_LIBS"] = reproduction_r_libs
            quarto = shutil.which("quarto")
            self.assertIsNotNone(quarto, "Quarto is missing from the locked environment.")
            reproduction = subprocess.run(
                [
                    quarto,
                    "render",
                    "report.qmd",
                    "--to",
                    "html",
                    "--output",
                    "reproduced.html",
                ],
                cwd=project / "output",
                text=True,
                capture_output=True,
                check=False,
                env=reproduction_env,
            )
            self.assertEqual(
                reproduction.returncode,
                0,
                msg=f"Reproduction report failed.\n{reproduction.stdout}\n{reproduction.stderr}",
            )
            self.assertTrue((project / "output" / "reproduced.html").is_file())


if __name__ == "__main__":
    unittest.main()
