from __future__ import annotations

import io
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from auto_zcurve.credentials import CredentialStoreUnavailable
from auto_zcurve.models import ModelOption
from auto_zcurve.projects import create_project
from auto_zcurve.runner import RunCancelled, RunSummary
from auto_zcurve.web import create_app


TOKEN = "test-session-token"
ORIGIN = {"Origin": "http://127.0.0.1"}


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_environment = patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(self.root / "config")},
        )
        self.config_environment.start()
        self.app = create_app(token=TOKEN, projects_root=self.root)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")

    def tearDown(self):
        self.client.close()
        self.config_environment.stop()
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

    def test_open_project_folder_uses_local_os_opener(self):
        project = create_project("Open folder", root=self.root)
        with patch("auto_zcurve.web.platform.system", return_value="Darwin"), patch(
            "auto_zcurve.web.subprocess.Popen"
        ) as opener:
            response = self.client.post(
                f"/{TOKEN}/projects/{project.project_id}/open-folder",
                headers=ORIGIN,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"opened": True})
        opener.assert_called_once()
        self.assertEqual(opener.call_args.args[0], ["open", str(project.path)])

    def test_open_project_folder_reports_missing_os_opener(self):
        project = create_project("Open folder failure", root=self.root)
        with patch("auto_zcurve.web.platform.system", return_value="Linux"), patch(
            "auto_zcurve.web.subprocess.Popen", side_effect=FileNotFoundError("xdg-open")
        ):
            response = self.client.post(
                f"/{TOKEN}/projects/{project.project_id}/open-folder",
                headers=ORIGIN,
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("Could not open the project folder", response.json()["detail"])

    def test_vendored_ui_assets_are_served_from_the_local_allowlist(self):
        for filename, content_type in (
            ("app.css", "text/css"),
            ("app.js", "text/javascript"),
        ):
            response = self.client.get(f"/{TOKEN}/static/{filename}")
            self.assertEqual(response.status_code, 200)
            self.assertIn(content_type, response.headers["content-type"])
        for filename, content_type in (
            ("basecoat.min.css", "text/css"),
            ("basecoat.min.js", "text/javascript"),
            ("basecoat.LICENSE.txt", "text/plain"),
        ):
            response = self.client.get(f"/{TOKEN}/static/vendor/{filename}")
            self.assertEqual(response.status_code, 200)
            self.assertIn(content_type, response.headers["content-type"])
        self.assertEqual(
            self.client.get(f"/{TOKEN}/static/vendor/not-allowed.css").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/{TOKEN}/static/not-allowed.css").status_code,
            404,
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
        self.assertIn("<h1>Projects</h1>", response.text)
        self.assertIn('data-variant="warning"', response.text)
        self.assertIn("It is not suitable for producing publication-quality data.", response.text)
        self.assertIn("Search projects", response.text)
        self.assertIn("/static/vendor/basecoat.min.css", response.text)
        self.assertNotIn("cdn.jsdelivr.net", response.text)
        self.assertIn('data-theme-value="system"', response.text)
        self.assertIn(f'class="app-bar-brand" href="/{TOKEN}/"', response.text)
        self.assertNotIn("status-dot-animated", response.text)
        self.assertIn(f'href="/{TOKEN}/settings"', response.text)
        self.assertEqual(response.text.count(f'href="/{TOKEN}/settings"'), 1)
        self.assertIn("data-project-pagination", response.text)
        self.assertEqual(response.text.count("data-project-row"), 10)
        self.assertNotIn("Evidence synthesis, without the busywork", response.text)
        self.assertNotIn("Turn a folder of papers", response.text)
        self.assertNotIn('configuration-link-card', response.text)

        payload = self.client.get(f"/{TOKEN}/api/projects").json()
        self.assertEqual(len(payload), 12)
        self.assertEqual(
            set(payload[0]),
            {"id", "name", "pdf_count", "failed_count", "total_tokens", "has_report"},
        )

    def test_update_notice_uses_the_info_variant(self):
        self.app.state.runtime.update_version = "0.12.4"

        response = self.client.get(f"/{TOKEN}/")

        self.assertIn('<div class="alert" data-variant="info" role="status">', response.text)
        self.assertIn(
            'href="https://shaheedazaad.github.io/auto-zcurve/" target="_blank" rel="noopener">auto-zcurve 0.12.4 is available</a>',
            response.text,
        )

    def test_settings_page_defaults_to_cloudflare_and_persists_app_defaults(self):
        page = self.client.get(f"/{TOKEN}/settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-page="settings"', page.text)
        self.assertIn('name="parallel_requests" type="number" min="1" max="32" value="1"', page.text)
        self.assertIn('name="request_delay_sec" type="number" min="0" max="3600" value="30"', page.text)
        self.assertNotIn('value="cloudflare-ai"', page.text)
        self.assertIn('id="key-dialog-title-gemini"', page.text)
        self.assertIn('id="default-gemini-model"', page.text)
        self.assertIn('id="default-openrouter-model"', page.text)
        self.assertIn(f'name="default_gemini_model"', page.text)

        saved = self.client.post(
            f"/{TOKEN}/settings",
            data={
                "pdf_parser": "cloudflare-ai",
                "parallel_requests": "4",
                "request_delay_sec": "45",
                "request_timeout_sec": "900",
                "max_upload_size_mb": "64",
                "reasoning_effort": "low",
                "default_gemini_model": "gemini-3.1-flash-lite",
                "default_openrouter_model": "openai/gpt-5",
            },
            headers=ORIGIN,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["pdf_parser"], "cloudflare-ai")
        self.assertEqual(saved.json()["reasoning_effort"], "low")
        self.assertEqual(saved.json()["request_delay_sec"], 45)
        self.assertEqual(saved.json()["default_gemini_model"], "gemini-3.1-flash-lite")
        self.assertEqual(saved.json()["default_openrouter_model"], "openai/gpt-5")

        reopened = self.client.get(f"/{TOKEN}/settings")
        self.assertIn('value="gemini-3.1-flash-lite"', reopened.text)
        self.assertIn('value="openai/gpt-5"', reopened.text)

        project = create_project("Configured defaults", root=self.root)
        project_page = self.client.get(f"/{TOKEN}/projects/{project.project_id}")
        self.assertIn("up to 64 MB each", project_page.text)
        self.assertIn('value="gemini-3.1-flash-lite"', project_page.text)

    def test_settings_rejects_unknown_parser(self):
        response = self.client.post(
            f"/{TOKEN}/settings",
            data={"pdf_parser": "mystery"},
            headers=ORIGIN,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported PDF parser", response.json()["detail"])

    def test_settings_can_remove_saved_and_session_key(self):
        self.app.state.runtime.session_api_keys["openrouter"] = "session-key"
        with patch("auto_zcurve.web.delete_saved_api_key", return_value=True) as delete_key:
            response = self.client.post(
                f"/{TOKEN}/credentials/delete",
                data={"provider": "openrouter"},
                headers=ORIGIN,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["deleted"])
        self.assertIsNone(self.app.state.runtime.api_key("openrouter"))
        delete_key.assert_called_once_with("openrouter")

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
        self.assertLess(response.text.index('data-tab-panel="overview"'), response.text.index('data-tab-panel="sources"'))
        self.assertIn('id="panel-overview" role="tabpanel" aria-labelledby="tab-overview" data-tab-panel="overview">', response.text)
        self.assertIn('id="panel-extraction-setup" role="tabpanel" aria-labelledby="tab-extraction-setup" data-tab-panel="extraction-setup" hidden>', response.text)
        self.assertIn(">Extraction instructions</button>", response.text)
        self.assertIn('id="panel-schema" role="tabpanel" aria-labelledby="tab-schema" data-tab-panel="schema" hidden>', response.text)
        self.assertIn("article-09.pdf", response.text)
        self.assertNotIn("article-10.pdf", response.text)
        self.assertIn("data-article-pagination", response.text)
        self.assertIn('list="gemini-model-options"', response.text)
        self.assertIn('value="gemini-3.6-flash"', response.text)
        self.assertIn('value="gemini-3-flash-preview"', response.text)
        self.assertIn("data-run-credential-prompt", response.text)
        self.assertNotIn("data-key-notice", response.text)
        self.assertIn('<span class="badge article-status" data-variant="outline">Ready</span>', response.text)
        self.assertIn('class="file-icon article-file-icon"', response.text)
        self.assertIn('id="project-actions-trigger"', response.text)
        self.assertIn('value="gemini" data-key-ready="false" data-saved-key="', response.text)
        self.assertIn(
            f'/{TOKEN}/projects/{project.project_id}/schema',
            response.text,
        )

    def test_project_credential_prompt_links_to_central_settings(self):
        project = create_project("Provider credentials", root=self.root)
        (project.path / "sources" / "study.pdf").write_bytes(b"%PDF-fixture")
        self.app.state.runtime.session_api_keys["openrouter"] = "openrouter-key"

        response = self.client.get(f"/{TOKEN}/projects/{project.project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Add a Gemini API key to run", response.text)
        self.assertIn(f'href="/{TOKEN}/settings">Enter API key in Settings</a>', response.text)
        self.assertIn("data-run-actions hidden", response.text)
        self.assertIn('value="openrouter" data-key-ready="true"', response.text)
        self.assertIn("syncProviderCredentialState", (Path("auto_zcurve/static/app.js")).read_text(encoding="utf-8"))

    def test_project_with_saved_key_shows_run_actions_without_unlocking(self):
        project = create_project("Run panel with saved key", root=self.root)
        (project.path / "sources" / "study.pdf").write_bytes(b"%PDF-fixture")
        with patch("auto_zcurve.web.saved_api_key_configured", return_value=True):
            response = self.client.get(f"/{TOKEN}/projects/{project.project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("data-run-actions", response.text)
        self.assertNotIn("data-run-actions hidden", response.text)
        self.assertNotIn("Unlock", response.text)
        self.assertNotIn('class="card key-notice"', response.text)

    def test_partial_project_offers_to_continue_remaining_pdfs(self):
        project = create_project("Resume extraction", root=self.root)
        for name in ("finished.pdf", "remaining.pdf"):
            (project.path / "sources" / name).write_bytes(b"%PDF-fixture")
        (project.path / "output" / "extractions.json").write_text(
            '[{"source_name":"finished.pdf","status":"ok","effects":1}]\n',
            encoding="utf-8",
        )
        self.app.state.runtime.session_api_key = "session-key"

        response = self.client.get(f"/{TOKEN}/projects/{project.project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Continue processing 1 PDF", response.text)
        self.assertIn("data-run-actions", response.text)
        self.assertNotIn("data-run-actions hidden", response.text)

    def test_project_outputs_can_be_cleared_and_project_can_be_deleted(self):
        project = create_project("Lifecycle actions", root=self.root)
        source = project.path / "sources" / "study.pdf"
        source.write_bytes(b"%PDF-fixture")
        output = project.path / "output"
        (output / "extractions.json").write_text(
            '[{"source_name":"study.pdf","status":"ok"}]\n',
            encoding="utf-8",
        )
        (output / "report.html").write_text("<h1>Report</h1>", encoding="utf-8")
        instructions = (project.path / "extraction_instructions.md").read_text(encoding="utf-8")
        schema = (project.path / "extraction_schema.yml").read_text(encoding="utf-8")

        reset = self.client.post(
            f"/{TOKEN}/projects/{project.project_id}/reset",
            headers=ORIGIN,
        )
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.json(), {"reset": True})
        self.assertTrue(source.exists())
        self.assertEqual((project.path / "extraction_instructions.md").read_text(encoding="utf-8"), instructions)
        self.assertEqual((project.path / "extraction_schema.yml").read_text(encoding="utf-8"), schema)
        self.assertEqual(list(output.rglob("*")), [output / "raw"])

        deleted = self.client.post(
            f"/{TOKEN}/projects/{project.project_id}/delete",
            headers=ORIGIN,
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"deleted": True})
        self.assertFalse(project.path.exists())
        self.assertEqual(
            self.client.get(f"/{TOKEN}/projects/{project.project_id}").status_code,
            404,
        )

    def test_project_can_be_renamed(self):
        project = create_project("Original name", root=self.root)

        renamed = self.client.post(
            f"/{TOKEN}/projects/{project.project_id}/rename",
            data={"name": "  Updated   Name  "},
            headers=ORIGIN,
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json(), {"renamed": True, "name": "Updated Name"})

        page = self.client.get(f"/{TOKEN}/projects/{project.project_id}")
        self.assertIn("<h1>Updated Name</h1>", page.text)

        rejected = self.client.post(
            f"/{TOKEN}/projects/{project.project_id}/rename",
            data={"name": "   "},
            headers=ORIGIN,
        )
        self.assertEqual(rejected.status_code, 400)

    def test_project_template_tolerates_hot_reload_without_saved_key_status(self):
        project = create_project("Hot reload", root=self.root)

        from auto_zcurve import web as web_module

        original_context = web_module._project_context

        def legacy_context(request, runtime, managed_project):
            context = original_context(request, runtime, managed_project)
            context.pop("provider_saved_key_status")
            return context

        with patch("auto_zcurve.web._project_context", side_effect=legacy_context):
            response = self.client.get(f"/{TOKEN}/projects/{project.project_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn('value="openrouter" data-key-ready="false" data-saved-key="false"', response.text)

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

    def test_settings_page_does_not_read_saved_api_key(self):
        with patch("auto_zcurve.web.saved_api_key_configured", return_value=True):
            with patch(
                "auto_zcurve.web.load_saved_api_key",
                side_effect=AssertionError("Keychain must not be read while rendering"),
            ):
                response = self.client.get(f"/{TOKEN}/settings")
                project = create_project("No passive Keychain access", root=self.root)
                project_response = self.client.get(f"/{TOKEN}/projects/{project.project_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(project_response.status_code, 200)
        self.assertIn(">Unlock<", response.text)
        self.assertIn("never read until you explicitly unlock", response.text)
        self.assertNotIn('placeholder="••••••••••••"', response.text)

    def test_settings_explains_when_a_provider_has_no_saved_key(self):
        with patch("auto_zcurve.web.saved_api_key_configured", return_value=False):
            response = self.client.get(f"/{TOKEN}/settings")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(">Unlock<", response.text)
        self.assertIn("No key stored", response.text)
        self.assertIn('data-open-key-dialog="gemini"', response.text)

    def test_saved_api_key_is_read_only_after_explicit_unlock(self):
        with patch("auto_zcurve.web.load_saved_api_key", return_value="saved-secret-key") as load_key:
            response = self.client.post(f"/{TOKEN}/credentials/load", headers=ORIGIN)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["loaded"])
        self.assertEqual(self.app.state.runtime.session_api_key, "saved-secret-key")
        load_key.assert_called_once_with()
        self.assertNotIn("saved-secret-key", response.text)

    def test_run_auto_loads_saved_key_without_explicit_unlock(self):
        project = create_project("Auto unlock on run", root=self.root)
        (project.path / "sources" / "study.pdf").write_bytes(b"%PDF-fixture")

        with (
            patch("auto_zcurve.web.saved_api_key_configured", return_value=True),
            patch("auto_zcurve.web.load_saved_api_key", return_value="saved-secret-key") as load_key,
            patch("auto_zcurve.web.run_preflight"),
            patch("auto_zcurve.web.run_project"),
        ):
            response = self.client.post(
                f"/{TOKEN}/projects/{project.project_id}/run",
                data={"model": "gemini-3.5-flash"},
                headers=ORIGIN,
            )
        self.assertEqual(response.status_code, 202)
        load_key.assert_called_once()
        self.assertEqual(self.app.state.runtime.session_api_key, "saved-secret-key")

    def test_run_without_saved_or_session_key_still_fails(self):
        project = create_project("No key anywhere", root=self.root)
        (project.path / "sources" / "study.pdf").write_bytes(b"%PDF-fixture")

        with patch("auto_zcurve.web.saved_api_key_configured", return_value=False):
            response = self.client.post(
                f"/{TOKEN}/projects/{project.project_id}/run",
                data={"model": "gemini-3.5-flash"},
                headers=ORIGIN,
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn("API key is required", response.json()["detail"])

    def test_openrouter_credentials_and_filtered_model_catalog_are_separate(self):
        response = self.client.post(
            f"/{TOKEN}/credentials",
            data={"provider": "openrouter", "api_key": "openrouter-session-key"},
            headers=ORIGIN,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider"], "openrouter")
        self.assertEqual(self.app.state.runtime.api_key("openrouter"), "openrouter-session-key")
        self.assertIsNone(self.app.state.runtime.api_key("gemini"))

        with patch(
            "auto_zcurve.web.list_live_models",
            return_value=[
                ModelOption(
                    "deepseek/model",
                    "DeepSeek Model",
                    provider="openrouter",
                    input_mode="local_markdown",
                    context_length=163840,
                )
            ],
        ) as live_models:
            catalog = self.client.get(f"/{TOKEN}/api/models/openrouter")
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.json()[0]["id"], "deepseek/model")
        self.assertEqual(catalog.json()[0]["input_mode"], "cloudflare_pdf")
        self.assertEqual(catalog.json()[0]["context_length"], 163840)
        live_models.assert_called_once_with("openrouter-session-key", "openrouter")

    def test_retry_uses_the_provider_and_model_currently_selected_in_the_page(self):
        project = create_project("Retry with replacement model", root=self.root)
        self.app.state.runtime.session_api_keys["openrouter"] = "openrouter-session-key"

        with (
            patch.object(self.app.state.runtime, "start_job") as start_job,
            patch(
                "auto_zcurve.web.validate_model_option",
                return_value=ModelOption(
                    "vendor/compatible", "Compatible", provider="openrouter", input_mode="cloudflare_pdf"
                ),
            ),
        ):
            start_job.return_value.status = "queued"
            response = self.client.post(
                f"/{TOKEN}/projects/{project.project_id}/retry",
                data={
                    "provider": "openrouter",
                    "model": "vendor/compatible",
                    "parallel_requests": "3",
                },
                headers=ORIGIN,
            )

        self.assertEqual(response.status_code, 202)
        settings = start_job.call_args.args[2]
        self.assertEqual(settings.provider, "openrouter")
        self.assertEqual(settings.primary_model, "vendor/compatible")
        self.assertEqual(settings.parallel_requests, 3)
        self.assertEqual(settings.pdf_parser, "native")

        with patch.object(self.app.state.runtime, "start_job") as start_job:
            rejected = self.client.post(
                f"/{TOKEN}/projects/{project.project_id}/retry",
                data={
                    "provider": "openrouter",
                    "model": "google/gemini-3.7-flash:batch",
                },
                headers=ORIGIN,
            )
        self.assertEqual(rejected.status_code, 409)
        self.assertIn("batch-only models", rejected.json()["detail"])
        start_job.assert_not_called()

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
        self.assertIn('class="panel metric-card"', project_page.text)
        self.assertIn('class="metric-value">0.434', project_page.text)
        self.assertIn("0.434", project_page.text)
        self.assertIn("usable z-curve inputs", project_page.text)
        self.assertIn("Key estimates", project_page.text)
        self.assertIn(f"/{TOKEN}/projects/{project.project_id}/zcurve-plot", project_page.text)
        self.assertLess(project_page.text.index("zcurve-plot"), project_page.text.index("Key estimates"))
        self.assertLess(project_page.text.index('data-tab-panel="overview"'), project_page.text.index('data-tab-panel="sources"'))
        plot = self.client.get(f"/{TOKEN}/projects/{project.project_id}/zcurve-plot")
        self.assertEqual(plot.status_code, 200)
        self.assertEqual(plot.headers["content-type"], "image/png")
        parser_cache = project.path / ".auto_zcurve" / "cache" / "documents"
        parser_cache.mkdir(parents=True)
        (parser_cache / "study.md").write_text("private parsed article text", encoding="utf-8")
        raw_response = project.path / "output" / "raw" / "study.response.txt"
        raw_response.write_text('{"effects": []}\nEXTRA', encoding="utf-8")
        archive_response = self.client.get(f"/{TOKEN}/projects/{project.project_id}/results.zip")
        self.assertEqual(archive_response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
            self.assertIn("extraction_schema.yml", archive.namelist())
            self.assertIn("extraction_instructions.md", archive.namelist())
            self.assertIn("output/report.html", archive.namelist())
            self.assertIn("output/report.qmd", archive.namelist())
            self.assertIn("output/zcurve_reproduction_settings.csv", archive.namelist())
            self.assertIn("output/zcurve_plot.png", archive.namelist())
            self.assertIn("output/raw/study.response.txt", archive.namelist())
            self.assertFalse(any(".auto_zcurve" in name for name in archive.namelist()))
            self.assertNotIn("session-key", "\n".join(archive.namelist()))
            rendered_instructions = archive.read("extraction_instructions.md").decode("utf-8")
            self.assertNotIn("{{", rendered_instructions)
            self.assertIn("Effects of interest", rendered_instructions)

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

        page = self.client.get(f"/{TOKEN}/projects/{project.project_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="panel-schema" role="tabpanel" aria-labelledby="tab-schema" data-tab-panel="schema" hidden>', page.text)
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
