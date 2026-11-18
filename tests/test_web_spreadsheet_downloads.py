import csv
import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from auto_zcurve import web as w
from auto_zcurve.projects import create_project

DISCLOSURE = (
    'source_name,quote,statistic\n'
    'evil.pdf,"=HYPERLINK(""http://x"",""click"")",-1.25\n'
    'study.pdf,"  +cmd|\' /C calc\'!A0",-1.25e-3\n'
)
RUN_LOG = 'source_name,status,parser_warnings\nevil.pdf,ok,@SUM(1)\n'


class SpreadsheetDownloadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "config")})
        env.start()
        self.addCleanup(env.stop)
        self.project = create_project("Spreadsheet", root=root / "projects")
        self.output = self.project.path / "output"
        (self.output / "disclosure_table.csv").write_text(DISCLOSURE, encoding="utf-8")
        (self.output / "run_log.csv").write_text(RUN_LOG, encoding="utf-8")
        app = w.create_app(token="token", projects_root=root / "projects")
        self.base = "/token/projects/" + self.project.project_id
        self.client = TestClient(app, base_url="http://127.0.0.1")
        self.addCleanup(self.client.close)

    def snapshot(self):
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(self.output.iterdir())
            if path.is_file()
        }

    def test_disclosure_table_download_is_escaped_with_bom_and_crlf(self):
        response = self.client.get(self.base + "/spreadsheet/disclosure_table.csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/csv; charset=utf-8")
        self.assertIn(
            'filename="disclosure_table.spreadsheet.csv"',
            response.headers["content-disposition"],
        )
        body = response.content
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", body.replace(b"\r\n", b""))
        rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"), newline="")))
        self.assertEqual(
            rows,
            [
                ["source_name", "quote", "statistic"],
                ["evil.pdf", "'=HYPERLINK(\"http://x\",\"click\")", "-1.25"],
                ["study.pdf", "'  +cmd|' /C calc'!A0", "-1.25e-3"],
            ],
        )

    def test_run_log_download_is_escaped(self):
        response = self.client.get(self.base + "/spreadsheet/run_log.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="run_log.spreadsheet.csv"', response.headers["content-disposition"])
        self.assertEqual(
            response.content,
            b"\xef\xbb\xbfsource_name,status,parser_warnings\r\nevil.pdf,ok,'@SUM(1)\r\n",
        )

    def test_unknown_or_missing_files_return_404(self):
        for name in ["extractions.json", "report.html", "..%2Fextraction_schema.yml", "zcurve_reproduction_settings.csv"]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self.base + "/spreadsheet/" + name).status_code, 404)
        self.assertEqual(
            self.client.get("/token/projects/" + "f" * 16 + "/spreadsheet/run_log.csv").status_code,
            404,
        )

    def test_canonical_files_and_results_zip_are_unchanged(self):
        before = self.snapshot()
        archive_before = self.client.get(self.base + "/results.zip").content
        for name in ["disclosure_table.csv", "run_log.csv"]:
            self.assertEqual(self.client.get(self.base + "/spreadsheet/" + name).status_code, 200)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.output / "disclosure_table.csv").read_text(encoding="utf-8"), DISCLOSURE)
        archive_after = self.client.get(self.base + "/results.zip").content
        with zipfile.ZipFile(io.BytesIO(archive_before)) as first, zipfile.ZipFile(io.BytesIO(archive_after)) as second:
            self.assertEqual(first.namelist(), second.namelist())
            self.assertFalse(any("spreadsheet" in name for name in second.namelist()))
            for name in first.namelist():
                self.assertEqual(first.read(name), second.read(name))
            self.assertEqual(second.read("output/disclosure_table.csv"), DISCLOSURE.encode("utf-8"))

    def test_project_page_links_to_available_downloads(self):
        page = self.client.get(self.base).text
        self.assertIn("Spreadsheet-safe downloads", page)
        self.assertIn(self.base + "/spreadsheet/disclosure_table.csv", page)
        self.assertIn(self.base + "/spreadsheet/run_log.csv", page)
        self.assertNotIn(self.base + "/spreadsheet/zcurve_reproduction_settings.csv", page)
        self.assertIn("results/#spreadsheet-safe-downloads", page)
        (self.output / "zcurve_reproduction_settings.csv").write_text("setting,value\nseed,-1\n", encoding="utf-8")
        page = self.client.get(self.base).text
        self.assertIn(self.base + "/spreadsheet/zcurve_reproduction_settings.csv", page)
        response = self.client.get(self.base + "/spreadsheet/zcurve_reproduction_settings.csv")
        self.assertEqual(response.content, b"\xef\xbb\xbfsetting,value\r\nseed,-1\r\n")

    def test_project_page_omits_downloads_without_csv_outputs(self):
        for name in ["disclosure_table.csv", "run_log.csv"]:
            (self.output / name).unlink()
        page = self.client.get(self.base).text
        self.assertNotIn("Spreadsheet-safe downloads", page)


if __name__ == "__main__":
    unittest.main()
