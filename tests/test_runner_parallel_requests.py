from __future__ import annotations

import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from auto_zcurve.config import RunSettings, save_run_settings
from auto_zcurve.llm import ExtractionResult
from auto_zcurve.models import ModelOption
from auto_zcurve.runner import RunCancelled, run_project


class _Progress:
    def advance(self, description: str | None = None) -> None:
        pass


class _Console:
    def __init__(self) -> None:
        self.progress_description = ""

    def info(self, text: str) -> None:
        pass

    def warn(self, text: str) -> None:
        pass

    def success(self, text: str) -> None:
        pass

    @contextmanager
    def progress(self, total: int, description: str):
        self.progress_description = description
        yield _Progress()


class RunnerParallelRequestTests(unittest.TestCase):
    def test_openrouter_extractions_reach_the_provider_in_parallel(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            sources = project / "sources"
            sources.mkdir()
            for index in range(3):
                (sources / f"study-{index}.pdf").write_bytes(b"%PDF-fixture")
            (project / "extraction_schema.yml").write_text(
                "effects:\n  claim:\n    type: string\n",
                encoding="utf-8",
            )

            active = 0
            peak_active = 0
            lock = threading.Lock()
            all_started = threading.Event()

            def fake_extract_pdf(**kwargs):
                nonlocal active, peak_active
                with lock:
                    active += 1
                    peak_active = max(peak_active, active)
                    if active == 3:
                        all_started.set()
                if not all_started.wait(timeout=2):
                    raise AssertionError("Provider calls did not overlap")
                with lock:
                    active -= 1
                return ExtractionResult(
                    source_path=kwargs["source_path"],
                    source_name=kwargs["source_name"],
                    status="ok",
                    provider_used="openrouter",
                    model_used="deepseek/deepseek-v4-flash",
                    data={"effects": [{"claim": "Finding"}]},
                )

            console = _Console()
            settings = RunSettings(
                primary_model="deepseek/deepseek-v4-flash",
                provider="openrouter",
                parallel_requests=3,
                request_delay_sec=0,
            )
            model = ModelOption(
                name=settings.primary_model,
                display_name="DeepSeek V4 Flash",
                provider="openrouter",
                input_mode="cloudflare_pdf",
                context_length=1_048_576,
            )
            with (
                patch("auto_zcurve.runner.validate_model_option", return_value=model),
                patch("auto_zcurve.runner.extract_pdf", side_effect=fake_extract_pdf),
            ):
                summary = run_project(
                    project_dir=project,
                    settings=settings,
                    assume_yes=True,
                    interactive=False,
                    force=True,
                    skip_report=True,
                    console=console,
                    api_key="secret",
                )

        self.assertEqual(peak_active, 3)
        self.assertEqual(summary.successful_pdfs, 3)
        self.assertIn("3 parallel workers", console.progress_description)

    def test_cancellation_returns_without_waiting_for_active_provider_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            sources = project / "sources"
            sources.mkdir()
            (sources / "study.pdf").write_bytes(b"%PDF-fixture")
            (project / "extraction_schema.yml").write_text(
                "effects:\n  claim:\n    type: string\n",
                encoding="utf-8",
            )

            provider_started = threading.Event()
            release_provider = threading.Event()
            cancellation = threading.Event()
            outcome: dict[str, object] = {}

            def blocked_extract(**kwargs):
                provider_started.set()
                release_provider.wait(timeout=3)
                return ExtractionResult(
                    source_path=kwargs["source_path"],
                    source_name=kwargs["source_name"],
                    status="ok",
                    provider_used="openrouter",
                    model_used="deepseek/deepseek-v4-flash",
                    data={"effects": [{"claim": "Finding"}]},
                )

            settings = RunSettings(
                primary_model="deepseek/deepseek-v4-flash",
                provider="openrouter",
            )
            model = ModelOption(
                name=settings.primary_model,
                display_name="DeepSeek V4 Flash",
                provider="openrouter",
                input_mode="cloudflare_pdf",
                context_length=1_048_576,
            )

            def control_run():
                try:
                    run_project(
                        project_dir=project,
                        settings=settings,
                        assume_yes=True,
                        interactive=False,
                        force=True,
                        skip_report=True,
                        console=_Console(),
                        api_key="secret",
                        cancellation_event=cancellation,
                    )
                except BaseException as exc:
                    outcome["error"] = exc

            with (
                patch("auto_zcurve.runner.validate_model_option", return_value=model),
                patch("auto_zcurve.runner.extract_pdf", side_effect=blocked_extract),
            ):
                controller = threading.Thread(target=control_run)
                controller.start()
                self.assertTrue(provider_started.wait(timeout=2))
                started = time.monotonic()
                cancellation.set()
                controller.join(timeout=1)
                elapsed = time.monotonic() - started
                release_provider.set()

            self.assertFalse(controller.is_alive())
            self.assertLess(elapsed, 0.5)
            self.assertIsInstance(outcome.get("error"), RunCancelled)
            self.assertFalse((project / "output" / "extractions.json").exists())

    def test_restarted_run_processes_only_unfinished_pdfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            sources = project / "sources"
            sources.mkdir()
            first = sources / "finished.pdf"
            second = sources / "remaining.pdf"
            first.write_bytes(b"%PDF-first")
            second.write_bytes(b"%PDF-second")
            (project / "extraction_schema.yml").write_text(
                "effects:\n  claim:\n    type: string\n",
                encoding="utf-8",
            )
            output = project / "output"
            output.mkdir()
            (output / "extractions.json").write_text(
                '[{"source_name":"finished.pdf","status":"ok","effects":1}]\n',
                encoding="utf-8",
            )
            processed: list[str] = []

            def fake_extract_pdf(**kwargs):
                processed.append(kwargs["source_name"])
                return ExtractionResult(
                    source_path=kwargs["source_path"],
                    source_name=kwargs["source_name"],
                    status="ok",
                    provider_used="openrouter",
                    model_used="deepseek/deepseek-v4-flash",
                    data={"effects": [{"claim": "Finding"}]},
                )

            settings = RunSettings(
                primary_model="deepseek/deepseek-v4-flash",
                provider="openrouter",
            )
            save_run_settings(project, settings)
            model = ModelOption(
                name=settings.primary_model,
                display_name="DeepSeek V4 Flash",
                provider="openrouter",
                input_mode="cloudflare_pdf",
                context_length=1_048_576,
            )
            with (
                patch("auto_zcurve.runner.validate_model_option", return_value=model),
                patch("auto_zcurve.runner.extract_pdf", side_effect=fake_extract_pdf),
            ):
                summary = run_project(
                    project_dir=project,
                    settings=settings,
                    assume_yes=True,
                    interactive=False,
                    force=False,
                    skip_report=True,
                    console=_Console(),
                    api_key="secret",
                )

        self.assertEqual(processed, ["remaining.pdf"])
        self.assertEqual(summary.successful_pdfs, 2)


if __name__ == "__main__":
    unittest.main()
