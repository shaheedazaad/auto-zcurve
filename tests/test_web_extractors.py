"""Extractors through the browser app's HTTP interface."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from auto_zcurve import credentials
from auto_zcurve.extractors import ExtractorError, get_extractor
from auto_zcurve.config import RunSettings, save_run_settings
from auto_zcurve.models import ModelOption
from auto_zcurve.projects import create_project
from auto_zcurve.runner import RunSummary
from auto_zcurve.web import create_app
from tests.test_key_vault import FakeKeyring


TOKEN = "test-session-token"
ORIGIN = {"Origin": "http://127.0.0.1"}
SENTINEL = "sk-sentinel-key-that-must-never-leak"
GEMINI_MODELS = [ModelOption("gemini-3.6-flash", "Gemini 3.6 Flash"), ModelOption("gemini-3.1-pro-preview", "Pro")]


class WebExtractorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_dir = self.root / "config" / "auto-zcurve"
        self.patches = [
            patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")}),
            patch.object(credentials, "_keyring_module", side_effect=lambda: self.keyring),
            patch("auto_zcurve.extractors.list_live_models", return_value=GEMINI_MODELS),
        ]
        self.keyring = FakeKeyring()
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.app = create_app(token=TOKEN, projects_root=self.root)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    def create(self, **values) -> dict:
        fields = {"name": "Lab Gemini", "provider": "gemini", "model": "gemini-3.6-flash", **values}
        response = self.client.post(f"/{TOKEN}/extractors", data=fields, headers=ORIGIN)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def project_with_source(self, name="Study"):
        project = create_project(name, root=self.root)
        (project.path / "sources" / "study.pdf").write_bytes(b"%PDF-fixture")
        return project

    def wait_for_job(self, project_id: str) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.client.get(f"/{TOKEN}/api/projects/{project_id}").json()["job"]
            if job and job["status"] in {"complete", "failed", "cancelled"}:
                return job
            time.sleep(0.01)
        self.fail("The job did not finish.")

    def test_two_gemini_extractors_each_run_with_their_own_key(self):
        first = self.create(name="Lab", api_key="key-lab")
        second = self.create(name="Personal", api_key="key-personal", model="gemini-3.1-pro-preview", parallel_requests="3")
        project = self.project_with_source()
        self.app.state.runtime.vault = credentials.KeyVault()  # a fresh session

        with patch("auto_zcurve.web.run_preflight"), patch("auto_zcurve.web.run_project", return_value=None) as run:
            response = self.client.post(
                f"/{TOKEN}/projects/{project.project_id}/run",
                data={"extractor_id": second["extractor"]["id"]},
                headers=ORIGIN,
            )
            self.assertEqual(response.status_code, 202, response.text)
            self.wait_for_job(project.project_id)

        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "key-personal")
        self.assertEqual((kwargs["settings"].primary_model, kwargs["settings"].parallel_requests), ("gemini-3.1-pro-preview", 3))
        self.assertEqual(kwargs["settings"].extractor_id, second["extractor"]["id"])
        self.assertNotEqual(first["extractor"]["id"], second["extractor"]["id"])
        page = self.client.get(f"/{TOKEN}/projects/{project.project_id}").text
        self.assertIn(f'value="{second["extractor"]["id"]}" selected', page)

    def test_keys_never_appear_in_files_or_responses(self):
        created = self.create(api_key=SENTINEL)
        project = self.project_with_source()
        self.client.post(f"/{TOKEN}/projects/{project.project_id}/settings", data={"extractor_id": created["extractor"]["id"]}, headers=ORIGIN)

        responses = [
            json.dumps(created),
            self.client.get(f"/{TOKEN}/settings").text,
            self.client.get(f"/{TOKEN}/projects/{project.project_id}").text,
            self.client.get(f"/{TOKEN}/api/projects/{project.project_id}").text,
        ]
        for text in responses:
            self.assertNotIn(SENTINEL, text)
        self.assertNotIn(SENTINEL, (self.config_dir / "extractors.json").read_text(encoding="utf-8"))
        self.assertEqual(self.keyring.vault(), {created["extractor"]["id"]: SENTINEL})

    def test_settings_and_project_pages_never_open_the_key_vault(self):
        created = self.create(api_key="key")
        project = self.project_with_source()
        self.keyring.reads = 0
        self.app.state.runtime.vault = credentials.KeyVault()

        self.assertEqual(self.client.get(f"/{TOKEN}/settings").status_code, 200)
        self.assertEqual(self.client.get(f"/{TOKEN}/projects/{project.project_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/{TOKEN}/").status_code, 200)

        self.assertEqual(self.keyring.reads, 0)
        self.assertIn("Lab Gemini", self.client.get(f"/{TOKEN}/settings").text)
        self.assertTrue(created["extractor"]["has_key"])

    def test_an_unknown_model_is_refused_when_a_key_is_given(self):
        response = self.client.post(
            f"/{TOKEN}/extractors",
            data={"name": "Typo", "provider": "gemini", "model": "gemini-typo", "api_key": "key"},
            headers=ORIGIN,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("isn't available", response.json()["detail"])
        self.assertEqual(self.keyring.writes, 0)

    def test_without_a_key_the_model_is_checked_at_run_time(self):
        created = self.create(model="gemini-anything")
        self.assertIn("checked when it runs", created["warning"])
        self.assertFalse(created["extractor"]["has_key"])

    def test_running_without_a_required_key_explains_what_to_do(self):
        created = self.create()
        project = self.project_with_source()
        response = self.client.post(
            f"/{TOKEN}/projects/{project.project_id}/run",
            data={"extractor_id": created["extractor"]["id"]},
            headers=ORIGIN,
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("Add an API key", response.json()["detail"])

    def test_running_needs_a_chosen_extractor(self):
        project = self.project_with_source()
        response = self.client.post(f"/{TOKEN}/projects/{project.project_id}/run", headers=ORIGIN)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Choose an extractor", response.json()["detail"])
        page = self.client.get(f"/{TOKEN}/projects/{project.project_id}").text
        self.assertIn("Create an extractor in Settings", page)

    def test_deleting_an_extractor_removes_its_key_and_flags_projects(self):
        keep = self.create(name="Keep", api_key="key-keep")
        doomed = self.create(name="Doomed", api_key="key-doomed")
        project = self.project_with_source()
        self.client.post(f"/{TOKEN}/projects/{project.project_id}/settings", data={"extractor_id": doomed["extractor"]["id"]}, headers=ORIGIN)

        response = self.client.post(f"/{TOKEN}/extractors/{doomed['extractor']['id']}/delete", headers=ORIGIN)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.keyring.vault(), {keep["extractor"]["id"]: "key-keep"})
        page = self.client.get(f"/{TOKEN}/projects/{project.project_id}").text
        self.assertIn("was deleted", page)

    def test_deleting_or_unkeying_a_migrated_extractor_removes_its_old_key_item(self):
        from dataclasses import replace
        from auto_zcurve.extractors import load_extractors, save_extractors

        for action in ("delete", "key/delete"):
            with self.subTest(action=action):
                item = "openai_compatible_api_key"
                created = self.create(name=f"Migrated {action}", provider="openai_compatible", model="m", base_url="http://h/v1")
                save_extractors([replace(e, legacy_credential=item) if e.id == created["extractor"]["id"] else e for e in load_extractors()])

                class LockedOldItem(FakeKeyring):
                    def get_password(self, service, username):
                        if username == item:
                            raise RuntimeError("locked")
                        return super().get_password(service, username)

                self.keyring = LockedOldItem({("auto-zcurve", item): "old-key"})
                self.app = create_app(token=TOKEN, projects_root=self.root)
                self.client = TestClient(self.app, base_url="http://127.0.0.1")

                response = self.client.post(f"/{TOKEN}/extractors/{created['extractor']['id']}/{action}", headers=ORIGIN)

                self.assertEqual(response.status_code, 200)
                self.assertNotIn(("auto-zcurve", item), self.keyring.items)
                self.assertEqual(load_extractors()[-1].legacy_credential if action == "key/delete" else "", "")

    def test_the_retry_choice_is_offered_saved_and_kept_when_omitted(self):
        page = self.client.get(f"/{TOKEN}/settings").text
        self.assertIn('<option value="rerun_all" selected>Re-run every article on the next run</option>', page)
        saved = self.client.post(f"/{TOKEN}/settings", data={"pdf_parser": "native", "max_upload_size_mb": "64", "retry_extractor_change": "keep_results"}, headers=ORIGIN)
        self.assertEqual(saved.status_code, 200)
        self.assertIn('<option value="keep_results" selected>', self.client.get(f"/{TOKEN}/settings").text)
        self.client.post(f"/{TOKEN}/settings", data={"pdf_parser": "native", "max_upload_size_mb": "32"}, headers=ORIGIN)
        from auto_zcurve.config import load_app_settings
        self.assertEqual((load_app_settings().max_upload_size_mb, load_app_settings().retry_extractor_change), (32, "keep_results"))

    def test_deleting_names_projects_that_use_the_extractor_before_confirming(self):
        doomed = self.create(name="Doomed", api_key="key-doomed")
        other = self.create(name="Other")
        doomed_id = doomed["extractor"]["id"]
        saved = self.project_with_source("Saved choice")
        self.client.post(f"/{TOKEN}/projects/{saved.project_id}/settings", data={"extractor_id": doomed_id}, headers=ORIGIN)
        last_run = self.project_with_source("Last run only")
        save_run_settings(last_run.path, RunSettings(primary_model="gemini-3.6-flash", provider="gemini", extractor_id=doomed_id))
        moved_on = self.project_with_source("Moved on")
        save_run_settings(moved_on.path, RunSettings(primary_model="gemini-3.6-flash", provider="gemini", extractor_id=doomed_id))
        self.client.post(f"/{TOKEN}/projects/{moved_on.project_id}/settings", data={"extractor_id": other["extractor"]["id"]}, headers=ORIGIN)
        self.project_with_source("Unconfigured")
        self.keyring.reads = 0
        self.app.state.runtime.vault = credentials.KeyVault()

        listed = self.client.get(f"/{TOKEN}/api/extractors/{doomed_id}/projects")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(sorted(listed.json()["projects"]), ["Last run only", "Saved choice"])
        self.assertEqual(self.client.get(f"/{TOKEN}/api/extractors/{other['extractor']['id']}/projects").json(), {"projects": ["Moved on"]})
        self.assertEqual(self.client.get(f"/{TOKEN}/api/extractors/{'f' * 8}/projects").status_code, 404)
        self.assertEqual(self.client.get(f"/{TOKEN}/settings").status_code, 200)
        self.assertEqual(self.keyring.reads, 0)

        deleted = self.client.post(f"/{TOKEN}/extractors/{doomed_id}/delete", headers=ORIGIN).json()
        self.assertEqual(sorted(deleted["projects"]), ["Last run only", "Saved choice"])

    def test_replacing_and_removing_a_key(self):
        created = self.create(api_key="old")
        extractor_id = created["extractor"]["id"]

        replaced = self.client.post(f"/{TOKEN}/extractors/{extractor_id}/key", data={"api_key": "new"}, headers=ORIGIN)
        self.assertEqual(replaced.status_code, 200)
        self.assertTrue(replaced.json()["saved"])
        self.assertEqual(self.keyring.vault(), {extractor_id: "new"})

        removed = self.client.post(f"/{TOKEN}/extractors/{extractor_id}/key/delete", headers=ORIGIN)
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.keyring.vault(), {})
        settings_page = self.client.get(f"/{TOKEN}/settings").text
        self.assertIn("No key", settings_page)

    def test_key_problems_are_reported_as_warnings(self):
        created = self.create()
        extractor_id = created["extractor"]["id"]
        self.keyring.items[("auto-zcurve", "extractor_keys")] = "{broken"
        self.app.state.runtime.vault = credentials.KeyVault()
        response = self.client.post(f"/{TOKEN}/extractors/{extractor_id}/key", data={"api_key": "k"}, headers=ORIGIN)
        self.assertEqual(response.json(), {"saved": False, "warning": "The key was not saved. " + credentials.KeyVault.UNLOCK_MESSAGE})
        self.assertEqual(self.client.post(f"/{TOKEN}/extractors/{extractor_id}/key/delete", headers=ORIGIN).status_code, 409)

        self.app.state.runtime.vault = credentials.KeyVault()
        with patch.object(credentials, "_keyring_module", side_effect=credentials.CredentialStoreUnavailable("none")):
            response = self.client.post(f"/{TOKEN}/extractors/{extractor_id}/key", data={"api_key": "k"}, headers=ORIGIN)
        self.assertIn("kept for this app session only", response.json()["warning"])

    def test_deleting_a_missing_extractor_never_scans_projects(self):
        self.project_with_source()
        with patch("auto_zcurve.web.project_extractor") as scan:
            response = self.client.post(f"/{TOKEN}/extractors/{'f' * 8}/delete", headers=ORIGIN)
        self.assertEqual(response.status_code, 404)
        scan.assert_not_called()
        # Deleted by another request between the check and the removal.
        doomed = self.create()["extractor"]["id"]
        with patch("auto_zcurve.web.delete_extractor", side_effect=ExtractorError("That extractor no longer exists.")):
            response = self.client.post(f"/{TOKEN}/extractors/{doomed}/delete", headers=ORIGIN)
        self.assertEqual(response.status_code, 404)

    def test_an_extractor_whose_key_cannot_be_removed_is_kept(self):
        created = self.create(api_key="k")
        plain = self.create(name="Plain")
        self.keyring.items[("auto-zcurve", "extractor_keys")] = "{broken"
        self.app.state.runtime.vault = credentials.KeyVault()
        reads = self.keyring.reads
        self.assertIsNone(self.client.post(f"/{TOKEN}/extractors/{plain['extractor']['id']}/delete", headers=ORIGIN).json()["warning"])
        self.assertEqual(self.keyring.reads, reads)
        response = self.client.post(f"/{TOKEN}/extractors/{created['extractor']['id']}/delete", headers=ORIGIN)
        self.assertEqual(response.status_code, 409)
        self.assertIn("was not deleted", response.json()["detail"])
        # Still listed, so removing it can be retried.
        self.assertIsNotNone(get_extractor(created["extractor"]["id"]))

    def test_a_store_that_refuses_the_key_says_so(self):
        class RefusingKeyring(FakeKeyring):
            def set_password(self, service, username, value):
                raise RuntimeError("write denied")

        self.keyring = RefusingKeyring()
        created = self.create(api_key="k")
        self.assertIn("credential store refused the key", created["warning"])

    def test_an_unreadable_extractors_file_stops_pages_without_overwriting_it(self):
        existing = self.create()["extractor"]["id"]
        path = self.config_dir / "extractors.json"
        path.write_text("{broken", encoding="utf-8")
        for response in (
            self.client.get(f"/{TOKEN}/settings"),
            self.client.post(f"/{TOKEN}/extractors", data={"name": "X", "provider": "gemini", "model": "m"}, headers=ORIGIN),
            self.client.post(f"/{TOKEN}/extractors/{existing}", data={"name": "X", "model": "m"}, headers=ORIGIN),
        ):
            self.assertEqual(response.status_code, 503)
            self.assertIn("Fix or move the file", response.json()["detail"])
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_changing_the_model_checks_it_with_the_saved_key(self):
        with_key = self.create(api_key="k")["extractor"]["id"]
        without_key = self.create(name="Keyless")["extractor"]["id"]
        edit = lambda extractor_id, model, name: self.client.post(
            f"/{TOKEN}/extractors/{extractor_id}", data={"name": name, "model": model}, headers=ORIGIN
        )

        with patch("auto_zcurve.extractors.list_live_models", side_effect=AssertionError("unchanged models are not checked")):
            self.assertEqual(edit(with_key, "gemini-3.6-flash", "Renamed only").status_code, 200)
        self.assertEqual(edit(with_key, "gemini-typo", "Lab Gemini").status_code, 400)
        ok = edit(with_key, "gemini-3.1-pro-preview", "Lab Gemini")
        self.assertEqual((ok.status_code, ok.json()["warning"]), (200, None))
        self.assertIn("checked when it runs", edit(without_key, "gemini-anything", "Keyless").json()["warning"])
        self.keyring.items[("auto-zcurve", "extractor_keys")] = "{broken"
        self.app.state.runtime.vault = credentials.KeyVault()
        self.assertIn("checked when it runs", edit(with_key, "gemini-3.6-flash", "Lab Gemini").json()["warning"])

    def test_update_and_duplicate(self):
        created = self.create(api_key="key")
        extractor_id = created["extractor"]["id"]

        updated = self.client.post(
            f"/{TOKEN}/extractors/{extractor_id}",
            data={"name": "Renamed", "model": "gemini-3.1-pro-preview", "service_tier": "standard"},
            headers=ORIGIN,
        )
        copy = self.client.post(f"/{TOKEN}/extractors/{extractor_id}/duplicate", headers=ORIGIN)

        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["extractor"]["name"], "Renamed")
        self.assertEqual(copy.json()["extractor"]["name"], "Renamed copy")
        self.assertFalse(copy.json()["extractor"]["has_key"])

    def test_retry_uses_the_projects_extractor(self):
        created = self.create(api_key="key")
        project = self.project_with_source()
        self.client.post(f"/{TOKEN}/projects/{project.project_id}/settings", data={"extractor_id": created["extractor"]["id"]}, headers=ORIGIN)

        with patch("auto_zcurve.web.run_preflight"), patch("auto_zcurve.web.retry_project", return_value=None) as retry:
            response = self.client.post(f"/{TOKEN}/projects/{project.project_id}/retry", headers=ORIGIN)
            self.assertEqual(response.status_code, 202, response.text)
            self.wait_for_job(project.project_id)
        self.assertEqual(retry.call_args.kwargs["api_key"], "key")

    def test_report_regeneration_needs_no_extractor_or_key(self):
        project = self.project_with_source()
        with patch("auto_zcurve.web.run_preflight"), patch(
            "auto_zcurve.web.regenerate_report", return_value=RunSummary(None, 0, 0, 0, 0, 0, 0, 0)
        ):
            response = self.client.post(f"/{TOKEN}/projects/{project.project_id}/regenerate-report", headers=ORIGIN)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(self.wait_for_job(project.project_id)["status"], "complete")
        self.assertEqual(self.keyring.reads, 0)


if __name__ == "__main__":
    unittest.main()
