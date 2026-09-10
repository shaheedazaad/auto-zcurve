from pathlib import Path
import json
import tempfile
import unittest

from auto_zcurve.artifacts import (
    append_run_log,
    load_extractions,
    load_run_log,
    parse_zcurve_summary,
    read_zcurve_summary,
    upsert_extraction,
)
from auto_zcurve.gemini import ExtractionResult


class ArtifactTests(unittest.TestCase):
    def test_load_extractions_recovers_from_corrupt_aggregate_using_raw_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            raw = project / "output" / "raw"
            raw.mkdir(parents=True)
            aggregate = project / "output" / "extractions.json"
            corrupt_content = '[{"source_name":"study.pdf" "status":"ok"}]'
            aggregate.write_text(corrupt_content, encoding="utf-8")
            (raw / "study-12345678.json").write_text(
                json.dumps({"source_name": "study.pdf", "status": "ok", "effects": 1}),
                encoding="utf-8",
            )

            records = load_extractions(project)

            self.assertEqual(records, [{"source_name": "study.pdf", "status": "ok", "effects": 1}])
            self.assertEqual(json.loads(aggregate.read_text(encoding="utf-8")), records)
            backups = list((project / "output").glob("extractions.corrupt-*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), corrupt_content)

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
                raw_response='{"effects":[]}\nEXTRA',
                repaired_response='{"effects":[]}',
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                provider_used="openrouter",
                provider_responses=[
                    {"attempt": 1, "response": {"id": "generation-1", "choices": []}}
                ],
            )

            records = upsert_extraction(project, result)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["effects"], 1)
            self.assertEqual(records[0]["input_tokens"], 100)
            self.assertTrue(any((project / "output" / "raw").glob("study-*.json")))
            response_files = list(
                (project / "output" / "raw").glob("study-*.provider-response.json")
            )
            self.assertEqual(len(response_files), 1)
            archived = json.loads(response_files[0].read_text(encoding="utf-8"))
            self.assertEqual(archived["responses"][0]["response"]["id"], "generation-1")
            self.assertIn("provider_response_path", records[0])
            response_text_files = list((project / "output" / "raw").glob("study-*.response.txt"))
            self.assertEqual(len(response_text_files), 1)
            self.assertEqual(response_text_files[0].read_text(encoding="utf-8"), '{"effects":[]}\nEXTRA')
            self.assertIn("raw_response_path", records[0])
            self.assertIn("repaired_response_path", records[0])

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

    def test_parse_zcurve_summary_extracts_execution_and_core_metrics(self):
        parsed = parse_zcurve_summary(
            """Bootstrap execution: parallel with 7 workers.

              Estimate  l.CI  u.CI
ERR              0.434 0.184 0.680
EDR              0.225 0.050 0.642

Fitted using values (ODR = 0.89, 95% CI [0.80, 0.95]).
"""
        )
        self.assertEqual(parsed["execution"], "Bootstrap execution: parallel with 7 workers.")
        self.assertEqual([item["code"] for item in parsed["metrics"]], ["ERR", "EDR", "ODR"])
        self.assertEqual(parsed["metrics"][0]["lower_ci"], "0.184")


if __name__ == "__main__":
    unittest.main()
