from pathlib import Path
import tempfile
import unittest

from auto_zcurve.artifacts import append_run_log, load_run_log, read_zcurve_summary, upsert_extraction
from auto_zcurve.gemini import ExtractionResult


class ArtifactTests(unittest.TestCase):
    def test_upsert_extraction_and_append_run_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            result = ExtractionResult(
                source_path=project / "sources" / "study.pdf",
                source_name="study.pdf",
                status="ok",
                model_used="gemini-3.5-flash",
                data={"effects": [{"reported_statistic": "t(38)=2.14"}]},
                raw_json='{"effects":[]}',
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
            )

            records = upsert_extraction(project, result)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["effects"], 1)
            self.assertEqual(records[0]["input_tokens"], 100)
            self.assertTrue(any((project / "output" / "raw").glob("study-*.json")))

            append_run_log(project, {"source_name": "study.pdf", "status": "ok"})
            rows = load_run_log(project)
            self.assertEqual(rows[0]["source_name"], "study.pdf")
            self.assertTrue((project / "output" / "run_log.csv").exists())

    def test_read_zcurve_summary_returns_text_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            summary_path = project / "output" / "zcurve_summary.txt"
            summary_path.parent.mkdir()
            summary_path.write_text("EDR 0.310\nODR 0.890\n", encoding="utf-8")

            self.assertEqual(read_zcurve_summary(project), "EDR 0.310\nODR 0.890")


if __name__ == "__main__":
    unittest.main()
