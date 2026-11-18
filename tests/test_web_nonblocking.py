from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from auto_zcurve import web
from auto_zcurve.models import ModelOption
from auto_zcurve.projects import create_project


TOKEN = "test-session-token"
ORIGIN = {"Origin": "http://127.0.0.1"}
OPTION = ModelOption("vendor/model", "Vendor Model", provider="openrouter", input_mode="cloudflare_pdf")


class SlowWorkDoesNotBlockOtherRequestsTests(unittest.TestCase):
    """Slow work in one request must not stop the app serving other requests."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")})
        environment.start()
        self.addCleanup(environment.stop)
        self.app = web.create_app(token=TOKEN, projects_root=self.root)
        self.runtime = self.app.state.runtime
        self.project = create_project("Slow work", root=self.root)

    def _slow(self, result, order: list[str], started: threading.Event, release: threading.Event):
        def slow(*_args, **_kwargs):
            started.set()
            release.wait(timeout=2)
            order.append("slow work finished")
            return result

        return slow

    def assert_other_request_served_meanwhile(self, target: str, result, send_slow_request):
        order: list[str] = []
        started = threading.Event()
        release = threading.Event()

        async def scenario():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                slow_request = asyncio.create_task(send_slow_request(client))
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                other = await client.get(f"/{TOKEN}/api/projects")
                order.append("other request served")
                release.set()
                return other, await slow_request

        with patch.object(web, target, side_effect=self._slow(result, order, started, release)):
            other, slow_response = asyncio.run(scenario())

        self.assertEqual(other.status_code, 200)
        self.assertEqual(order, ["other request served", "slow work finished"])
        return slow_response

    def test_another_request_is_served_while_an_openrouter_model_check_is_in_progress(self):
        with patch.object(self.runtime, "_load_key_from_store", return_value="key"):
            response = self.assert_other_request_served_meanwhile(
                "validate_model_option",
                OPTION,
                lambda client: client.post(
                    f"/{TOKEN}/api/models/openrouter/validate", data={"model": "vendor/model"}, headers=ORIGIN
                ),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "vendor/model")

    def test_starting_a_run_or_retry_with_openrouter_does_not_block_other_requests(self):
        for action in ("run", "retry"):
            with (
                self.subTest(action=action),
                patch.object(self.runtime, "_load_key_from_store", return_value="key"),
                patch.object(self.runtime, "start_job") as start_job,
            ):
                start_job.return_value.status = "queued"
                response = self.assert_other_request_served_meanwhile(
                    "validate_model_option",
                    OPTION,
                    lambda client: client.post(
                        f"/{TOKEN}/projects/{self.project.project_id}/{action}",
                        data={"provider": "openrouter", "model": "vendor/model"},
                        headers=ORIGIN,
                    ),
                )
                self.assertEqual(response.status_code, 202)

    def test_saving_openrouter_project_settings_does_not_block_other_requests(self):
        with patch.object(self.runtime, "_load_key_from_store", return_value="key"):
            response = self.assert_other_request_served_meanwhile(
                "validate_model_option",
                OPTION,
                lambda client: client.post(
                    f"/{TOKEN}/projects/{self.project.project_id}/settings",
                    data={"provider": "openrouter", "model": "vendor/model"},
                    headers=ORIGIN,
                ),
            )
        self.assertEqual(response.status_code, 200)

    def test_loading_the_model_catalogue_does_not_block_other_requests(self):
        self.runtime.session_api_keys["openrouter"] = "key"
        response = self.assert_other_request_served_meanwhile(
            "list_live_models",
            [OPTION],
            lambda client: client.get(f"/{TOKEN}/api/models/openrouter"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["id"], "vendor/model")

    def test_downloading_the_results_zip_does_not_block_other_requests(self):
        response = self.assert_other_request_served_meanwhile(
            "_results_zip",
            b"zip bytes",
            lambda client: client.get(f"/{TOKEN}/projects/{self.project.project_id}/results.zip"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"zip bytes")
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn('filename="Slow-work-results.zip"', response.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
