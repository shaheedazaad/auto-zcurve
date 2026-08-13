from __future__ import annotations

import io
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from auto_zcurve.credentials import CredentialStoreUnavailable
from auto_zcurve.projects import create_project
from auto_zcurve.runner import RunCancelled, RunSummary
from auto_zcurve.web import create_app


TOKEN = "test-session-token"
ORIGIN = {"Origin": "http://127.0.0.1"}


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = create_app(token=TOKEN, projects_root=self.root)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    def test_token_host_and_origin_protection(self):
        self.assertEqual(self.client.get("/").status_code, 404)
        self.assertEqual(self.client.get(f"/{TOKEN}/").status_code, 200)
        self.assertEqual(
            self.client.get(f"/{TOKEN}/", headers={"Host": "attacker.example"}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                f"/{TOKEN}/projects",
                data={"name": "Blocked"},
                headers={"Origin": "https://attacker.example"},
            ).status_code,
            403,
        )

    def test_create_upload_duplicates_and_refresh(self):
        response = self.client.post(
            f"/{TOKEN}/projects",
            data={"name": "Browser workflow"},
            headers=ORIGIN,
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        location = response.headers["location"]
        project_id = location.rsplit("/", 1)[-1]

        files = [
            ("files", ("same.pdf", b"%PDF-first", "application/pdf")),
            ("files", ("same.pdf", b"%PDF-second", "application/pdf")),
        ]
        uploaded = self.client.post(
            f"/{TOKEN}/projects/{project_id}/uploads",
            files=files,
            headers=ORIGIN,
        )
        self.assertEqual(uploaded.status_code, 200)
        self.assertEqual(uploaded.json()["saved"], ["same.pdf", "same (2).pdf"])

        status = self.client.get(f"/{TOKEN}/api/projects/{project_id}").json()
        self.assertEqual(status["pdf_count"], 2)
        reopened = TestClient(
            create_app(token=TOKEN, projects_root=self.root),
            base_url="http://127.0.0.1",
        )
        try:
            self.assertIn("Browser workflow", reopened.get(f"/{TOKEN}/").text)
        finally:
            reopened.close()

    def test_home_prioritizes_searchable_paginated_project_index(self):
        for index in range(12):
            create_project(f"Research project {index:02d}", root=self.root)

        response = self.client.get(f"/{TOKEN}/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Auto Z-Curve projects", response.text)
        self.assertIn("Search projects", response.text)
        self.assertIn("data-project-pagination", response.text)
        self.assertEqual(response.text.count('class="project-row"'), 10)
        self.assertLess(response.text.index("Your projects"), response.text.index("Configuration"))
        self.assertNotIn("Evidence synthesis, without the busywork", response.text)
        self.assertNotIn("Turn a folder of papers", response.text)
        self.assertIn('<details class="card configuration-panel">', response.text)

        payload = self.client.get(f"/{TOKEN}/api/projects").json()
        self.assertEqual(len(payload), 12)
        self.assertEqual(
            set(payload[0]),
            {"id", "name", "pdf_count", "failed_count", "total_tokens", "has_report"},
        )

    def test_invalid_upload_and_missing_project(self):
        project = create_project("Uploads", root=self.root)
        response = self.client.post(
            f"/{TOKEN}/projects/{project.project_id}/uploads",
            files={"files": ("notes.txt", b"hello", "text/plain")},
            headers=ORIGIN,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.client.get(f"/{TOKEN}/projects/../../etc").status_code,
            404,
        )

    def test_project_page_starts_with_empty_summary_and_paginates_articles(self):
        project = create_project("Compact project", root=self.root)
        for index in range(12):
            (project.path / "sources" / f"article-{index:02d}.pdf").write_bytes(b"%PDF-fixture")

        response = self.client.get(f"/{TOKEN}/projects/{project.project_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("No report yet", response.text)
        self.assertIn("Process some articles", response.text)
        self.assertLess(response.text.index("Analysis summary"), response.text.index("Source material"))
        self.assertIn('<details class="card instructions-card">', response.text)
        self.assertNotIn('<details class="card instructions-card" open', response.text)
        self.assertIn("article-09.pdf", response.text)
        self.assertNotIn("article-10.pdf", response.text)
        self.assertIn("data-article-pagination", response.text)
        self.assertIn('list="gemini-model-options"', response.text)
        self.assertIn('value="gemini-3.6-flash"', response.text)
        self.assertIn('value="gemini-3-flash-preview"', response.text)
        self.assertIn(
            f'/{TOKEN}/projects/{project.project_id}/schema',
            response.text,
        )

    def test_session_key_fallback_does_not_write_secret(self):
        with patch(
            "auto_zcurve.web.save_api_key",
            side_effect=CredentialStoreUnavailable("No secure store"),
        ):
            response = self.client.post(
                f"/{TOKEN}/credentials",
                data={"api_key": "top-secret-key", "remember": "yes"},
                headers=ORIGIN,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["session_only"])
        self.assertEqual(self.app.state.runtime.session_api_key, "top-secret-key")
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"top-secret-key", path.read_bytes())

    def test_home_does_not_read_saved_api_key(self):
        with patch("auto_zcurve.web.saved_api_key_configured", return_value=True):
            with patch(
                "auto_zcurve.web.load_saved_api_key",
                side_effect=AssertionError("Keychain must not be read while rendering"),
            ):
                response = self.client.get(f"/{TOKEN}/")
                project = create_project("No passive Keychain access", root=self.root)
                project_response = self.client.get(f"/{TOKEN}/projects/{project.project_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(project_response.status_code, 200)
        self.assertIn("Unlock previously saved key", response.text)
        self.assertIn("remains locked", response.text)
        self.assertNotIn('placeholder="••••••••••••"', response.text)

    def test_saved_api_key_is_read_only_after_explicit_unlock(self):
        with patch("auto_zcurve.web.load_saved_api_key", return_value="saved-secret-key") as load_key:
            response = self.client.post(f"/{TOKEN}/credentials/load", headers=ORIGIN)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["loaded"])
        self.assertEqual(self.app.state.runtime.session_api_key, "saved-secret-key")
        load_key.assert_called_once_with()
        self.assertNotIn("saved-secret-key", response.text)

    def test_run_progress_summary_and_results_download(self):
        project = create_project("Analysis", root=self.root)
        (project.path / "sources" / "study.pdf").write_bytes(b"%PDF-fixture")
        self.app.state.runtime.session_api_key = "session-key"

        def fake_run(**kwargs):
            output = kwargs["project_dir"] / "output"
            output.mkdir(exist_ok=True)
            (output / "report.html").write_text("<h1>Report</h1>", encoding="utf-8")
            (output / "report.qmd").write_text("---\ntitle: Report\n---\n", encoding="utf-8")
            (output / "zcurve_plot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (output / "zcurve_reproduction_settings.csv").write_text(
                "bootstrap_seed,bootstrap_iterations\n20260802,1000\n",
                encoding="utf-8",
            )
            (output / "disclosure_table.csv").write_text(
                "usable_for_zcurve\ntrue\nfalse\ntrue\n",
                encoding="utf-8",
            )
            (output / "zcurve_summary.txt").write_text(
                """Bootstrap execution: parallel with 2 workers.

              Estimate  l.CI  u.CI
ERR              0.434 0.184 0.680
EDR              0.225 0.050 0.642

Fitted using values (ODR = 0.89, 95% CI [0.80, 0.95]).
""",
                encoding="utf-8",
            )
            with kwargs["console"].progress(1, "Extracting PDFs") as progress:
                progress.advance("ok: study.pdf")
            return RunSummary(output / "report.html", 1, 0, 3, 2, 10, 4, 14)

        custom_model = "models/gemini-research-preview"
        with (
            patch("auto_zcurve.web.run_preflight"),
            patch("auto_zcurve.web.run_project", side_effect=fake_run) as run_mock,
        ):
            response = self.client.post(
                f"/{TOKEN}/projects/{project.project_id}/run",
                data={"model": custom_model, "parallel_requests": "2"},
                headers=ORIGIN,
            )
            self.assertEqual(response.status_code, 202)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                payload = self.client.get(f"/{TOKEN}/api/projects/{project.project_id}").json()
                if payload["job"]["status"] == "complete":
                    break
                time.sleep(0.01)
            self.assertEqual(payload["job"]["status"], "complete")
            self.assertEqual(payload["job"]["summary"]["total_tokens"], 14)
            self.assertEqual(
                run_mock.call_args.kwargs["settings"].primary_model,
                "gemini-research-preview",
            )
            events = self.client.get(
                f"/{TOKEN}/projects/{project.project_id}/events"
            ).text
            self.assertIn("event: progress", events)
            self.assertIn('"status": "complete"', events)

        report = self.client.get(f"/{TOKEN}/projects/{project.project_id}/report")
        self.assertEqual(report.status_code, 200)
        project_page = self.client.get(f"/{TOKEN}/projects/{project.project_id}")
        self.assertIn("Analysis summary", project_page.text)
        self.assertIn("Expected replication rate", project_page.text)
        self.assertIn("0.434", project_page.text)
        self.assertIn("usable z-curve inputs", project_page.text)
        self.assertIn("Key estimates", project_page.text)
        self.assertIn(f"/{TOKEN}/projects/{project.project_id}/zcurve-plot", project_page.text)
        self.assertLess(project_page.text.index("Key estimates"), project_page.text.index("zcurve-plot"))
        self.assertLess(project_page.text.index("Analysis summary"), project_page.text.index("Source material"))
        plot = self.client.get(f"/{TOKEN}/projects/{project.project_id}/zcurve-plot")
        self.assertEqual(plot.status_code, 200)
        self.assertEqual(plot.headers["content-type"], "image/png")
        archive_response = self.client.get(f"/{TOKEN}/projects/{project.project_id}/results.zip")
        self.assertEqual(archive_response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
            self.assertIn("output/report.html", archive.namelist())
            self.assertIn("output/report.qmd", archive.namelist())
            self.assertIn("output/zcurve_reproduction_settings.csv", archive.namelist())
            self.assertIn("output/zcurve_plot.png", archive.namelist())
            self.assertNotIn("session-key", "\n".join(archive.namelist()))

    def test_instruction_edit_requires_confirmation_and_clears_previous_analysis(self):
        project = create_project("Instruction editing", root=self.root)
        source = project.path / "sources" / "study.pdf"
        source.write_bytes(b"%PDF-fixture")
        output = project.path / "output"
        (output / "extractions.json").write_text(
            '[{"source_name":"study.pdf","status":"ok","effects":1}]\n',
            encoding="utf-8",
        )
        (output / "report.html").write_text("<h1>Old report</h1>", encoding="utf-8")

        blocked = self.client.post(
            f"/{TOKEN}/projects/{project.project_id}/instructions",
            data={"instructions": "# Revised extraction rules"},
            headers=ORIGIN,
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("Every PDF will need to be processed again", blocked.json()["detail"])
        self.assertTrue((output / "report.html").exists())

        confirmed = self.client.post(
            f"/{TOKEN}/projects/{project.project_id}/instructions",
            data={"instructions": "# Revised extraction rules", "confirm_reset": "yes"},
            headers=ORIGIN,
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json(), {"changed": True, "reset": True})
        self.assertTrue(source.exists())
        self.assertFalse((output / "report.html").exists())
        self.assertFalse((output / "extractions.json").exists())
        self.assertIn(
            "# Revised extraction rules",
            (project.path / "extraction_instructions.md").read_text(encoding="utf-8"),
        )
        status = self.client.get(f"/{TOKEN}/api/projects/{project.project_id}").json()
        self.assertEqual(status["articles"][0]["status"], "ready")
        self.assertFalse(status["has_report"])

    def test_schema_editor_validates_and_clears_previous_analysis(self):
        project = create_project("Schema editing", root=self.root)
        source = project.path / "sources" / "study.pdf"
        source.write_bytes(b"%PDF-fixture")
        output = project.path / "output"
        (output / "extractions.json").write_text("[]\n", encoding="utf-8")
        (output / "report.html").write_text("<h1>Old report</h1>", encoding="utf-8")
        schema_path = project.path / "extraction_schema.yml"
        original_schema = schema_path.read_text(encoding="utf-8")

        page = self.client.get(f"/{TOKEN}/projects/{project.project_id}/schema")
        self.assertEqual(page.status_code, 200)
        self.assertIn("What the schema controls", page.text)
        self.assertIn("Keep a reported statistic", page.text)
        self.assertIn("data-yaml-editor", page.text)
        self.assertIn("extraction_schema.yml", page.text)

        invalid = self.client.post(
            f"/{TOKEN}/projects/{project.project_id}/schema",
            data={"schema_text": "effects:\n  result: [\n", "confirm_reset": "yes"},
            headers=ORIGIN,
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("Invalid YAML at line", invalid.json()["detail"])
        self.assertEqual(schema_path.read_text(encoding="utf-8"), original_schema)
        self.assertTrue((output / "report.html").exists())

        revised_schema = (
            "name: revised\n"
            "effects:\n"
            "  focal_result:\n"
            "    type: string\n"
            "    role: reported_statistic\n"
            "    description: The focal statistical result.\n"
        )
        blocked = self.client.post(
            f"/{TOKEN}/projects/{project.project_id}/schema",
            data={"schema_text": revised_schema},
            headers=ORIGIN,
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("Every PDF will need to be processed again", blocked.json()["detail"])
        self.assertTrue((output / "report.html").exists())

        confirmed = self.client.post(
            f"/{TOKEN}/projects/{project.project_id}/schema",
            data={"schema_text": revised_schema, "confirm_reset": "yes"},
            headers=ORIGIN,
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json(), {"changed": True, "reset": True})
        self.assertEqual(schema_path.read_text(encoding="utf-8"), revised_schema)
        self.assertTrue(source.exists())
        self.assertFalse((output / "report.html").exists())
        self.assertFalse((output / "extractions.json").exists())

    def test_regenerate_report_uses_existing_artifacts_without_api_key(self):
        project = create_project("Regenerate", root=self.root)
        (project.path / "output" / "extractions.json").write_text(
            '[{"source_name":"study.pdf","status":"ok","effects":3}]\n',
            encoding="utf-8",
        )

        def fake_regenerate(**kwargs):
            output = kwargs["project_dir"] / "output"
            (output / "report.qmd").write_text("---\ntitle: Updated\n---\n", encoding="utf-8")
            (output / "report.html").write_text("<h1>Updated</h1>", encoding="utf-8")
            with kwargs["console"].progress(1, "Rendering report") as progress:
                progress.advance("Report rendered")
            return RunSummary(output / "report.html", 1, 0, 3, 2, 10, 4, 14)

        with (
            patch("auto_zcurve.web.run_preflight"),
            patch("auto_zcurve.web.regenerate_report", side_effect=fake_regenerate) as regenerate,
        ):
            response = self.client.post(
                f"/{TOKEN}/projects/{project.project_id}/regenerate-report",
                headers=ORIGIN,
            )
            self.assertEqual(response.status_code, 202)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                payload = self.client.get(f"/{TOKEN}/api/projects/{project.project_id}").json()
                if payload["job"]["status"] == "complete":
                    break
                time.sleep(0.01)

        self.assertEqual(payload["job"]["status"], "complete")
        regenerate.assert_called_once()
        self.assertIsNone(self.app.state.runtime.session_api_key)
        self.assertIn("Regenerate report", self.client.get(
            f"/{TOKEN}/projects/{project.project_id}"
        ).text)

    def test_cancel_and_secret_redaction(self):
        project = create_project("Cancellation", root=self.root)
        (project.path / "sources" / "study.pdf").write_bytes(b"%PDF-fixture")
        secret = "session-key-that-must-not-leak"
        self.app.state.runtime.session_api_key = secret

        def wait_for_cancel(**kwargs):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not kwargs["cancellation_event"].is_set():
                time.sleep(0.01)
            raise RunCancelled(f"cancelled with {secret}")

        with (
            patch("auto_zcurve.web.run_preflight"),
            patch("auto_zcurve.web.run_project", side_effect=wait_for_cancel),
        ):
            self.client.post(
                f"/{TOKEN}/projects/{project.project_id}/run",
                data={"model": "gemini-3.5-flash"},
                headers=ORIGIN,
            )
            cancelled = self.client.post(
                f"/{TOKEN}/projects/{project.project_id}/cancel",
                headers=ORIGIN,
            )
            self.assertEqual(cancelled.status_code, 200)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                job = self.client.get(
                    f"/{TOKEN}/api/projects/{project.project_id}"
                ).json()["job"]
                if job["status"] == "cancelled":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "cancelled")
            self.assertNotIn(secret, str(job))


if __name__ == "__main__":
    unittest.main()
