from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .artifacts import load_extractions
from .config import DEFAULTS, RunSettings, load_run_settings
from .console import CliConsole
from .models import fallback_models, list_live_models, normalize_model_name
from .preflight import PreflightError, run_preflight
from .runner import ensure_project_layout, retry_project, run_project
from .env import resolve_api_key
from .user_facing import classify_error, format_run_result


def _questionary():
    try:
        import questionary

        return questionary
    except Exception:
        return None


def prompt_project_dir() -> Path:
    q = _questionary()
    if q:
        answer = q.path("Project directory:", only_directories=True, default=".").ask()
    else:
        answer = input("Project directory [.] ").strip() or "."
    return Path(answer).expanduser().resolve()


def prompt_api_key(project_dir: Path, explicit_key: str | None, interactive: bool) -> str:
    try:
        return resolve_api_key(project_dir, explicit_key=explicit_key)
    except RuntimeError:
        if not interactive:
            raise

    q = _questionary()
    if q:
        answer = q.password("Gemini API key:").ask()
    else:
        import getpass

        answer = getpass.getpass("Gemini API key: ")

    return resolve_api_key(project_dir, explicit_key=answer)


def prompt_model(api_key: str, console: CliConsole) -> str:
    try:
        options = list_live_models(api_key)
        if not options:
            raise RuntimeError("No compatible Gemini generateContent models were returned.")
    except Exception as exc:
        console.warn(f"Live model discovery was unavailable: {exc}")
        options = fallback_models()

    choice_names = [option.name for option in options]
    q = _questionary()
    if q:
        primary = q.select("Primary extraction model:", choices=choice_names).ask()
        return normalize_model_name(primary)

    console.table("Available Models", ["#", "Model"], enumerate(choice_names, start=1))
    selected = int(input("Primary extraction model number: ").strip())
    primary = choice_names[selected - 1]
    return normalize_model_name(primary)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auto-zcurve")
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Run extraction for a project directory.")
    run.add_argument("project_dir", type=Path)
    run.add_argument("--yes", action="store_true", help="Accept setup defaults and do not prompt.")
    run.add_argument("--model", help="Primary Gemini model.")
    run.add_argument("--api-key", help="Gemini API key for this run. Not stored.")
    run.add_argument("--parallel", type=int, help="Number of PDFs to process at the same time.")
    run.add_argument("--force", action="store_true", help="Reprocess PDFs even if they already succeeded.")
    run.add_argument("--skip-preflight", action="store_true", help=argparse.SUPPRESS)
    run.add_argument("--skip-report", action="store_true", help="Skip Quarto report rendering.")

    retry = subparsers.add_parser("retry", help="Retry failed files from the latest project outputs.")
    retry.add_argument("project_dir", type=Path)
    retry.add_argument("--yes", action="store_true", help="Retry all failed files without prompting.")
    retry.add_argument("--model", help="Override primary Gemini model.")
    retry.add_argument("--api-key", help="Gemini API key for this retry. Not stored.")
    retry.add_argument("--parallel", type=int, help="Number of PDFs to retry at the same time.")
    retry.add_argument("--source", action="append", help="Retry only this source path relative to sources/.")
    retry.add_argument("--skip-preflight", action="store_true", help=argparse.SUPPRESS)
    retry.add_argument("--skip-report", action="store_true", help="Skip Quarto report rendering.")

    subparsers.add_parser("gui", help="Launch the Textual terminal GUI.")
    return parser


def _settings_from_args(
    args: argparse.Namespace,
    project_dir: Path,
    console: CliConsole,
    interactive: bool,
    api_key: str,
) -> RunSettings:
    existing = load_run_settings(project_dir)
    primary = args.model or (existing.primary_model if existing else None)

    if not primary and not interactive:
        raise RuntimeError("A Gemini model is required. Pass --model or run `auto-zcurve` for guided setup.")

    if not primary:
        primary = prompt_model(api_key, console)

    return RunSettings(
        primary_model=normalize_model_name(primary),
        request_timeout_sec=DEFAULTS["request_timeout_sec"],
        parallel_requests=max(1, int(args.parallel or DEFAULTS["parallel_requests"])),
        max_upload_size_mb=DEFAULTS["max_upload_size_mb"],
        effect_definition=existing.effect_definition if existing else None,
    )


def guided(console: CliConsole) -> int:
    console.title("Guided PDF extraction and z-curve reporting")
    project_dir = prompt_project_dir()
    args = argparse.Namespace(
        model=None,
        api_key=None,
        yes=False,
        force=False,
        skip_preflight=False,
        skip_report=False,
        parallel=None,
    )
    return run_command(args, project_dir, interactive=True, console=console)


def run_command(args: argparse.Namespace, project_dir: Path, interactive: bool, console: CliConsole) -> int:
    project_dir = project_dir.expanduser().resolve()
    ready = ensure_project_layout(
        project_dir,
        assume_yes=bool(args.yes),
        interactive=interactive and not args.yes,
        console=console,
    )
    if not ready:
        return 0
    if not args.skip_preflight:
        run_preflight(project_dir, interactive=interactive and not args.yes, console=console)
    prompt_allowed = interactive and not args.yes
    api_key = prompt_api_key(project_dir, explicit_key=args.api_key, interactive=prompt_allowed)
    settings = _settings_from_args(args, project_dir, console, interactive=prompt_allowed, api_key=api_key)
    summary = run_project(
        project_dir=project_dir,
        settings=settings,
        assume_yes=bool(args.yes),
        interactive=interactive and not args.yes,
        force=bool(args.force),
        skip_report=bool(args.skip_report),
        console=console,
        api_key=api_key,
    )
    if summary is None:
        return 0
    print_summary(summary, console, project_dir)
    if interactive and summary.failed_pdfs:
        retry_summary = offer_interactive_retry(args, project_dir, settings, console, api_key)
        if retry_summary:
            print_summary(retry_summary, console, project_dir)
            summary = retry_summary
    return 0 if summary.failed_pdfs == 0 else 1


def retry_command(args: argparse.Namespace, console: CliConsole) -> int:
    project_dir = args.project_dir.expanduser().resolve()
    if not args.skip_preflight:
        run_preflight(project_dir, interactive=False, console=console)
    api_key = prompt_api_key(project_dir, explicit_key=args.api_key, interactive=False)
    settings = _settings_from_args(args, project_dir, console, interactive=False, api_key=api_key)
    summary = retry_project(
        project_dir=project_dir,
        settings=settings,
        selected_sources=args.source,
        assume_yes=bool(args.yes),
        skip_report=bool(args.skip_report),
        console=console,
        api_key=api_key,
    )
    if summary is None:
        return 0
    print_summary(summary, console, project_dir)
    return 0 if summary.failed_pdfs == 0 else 1


def print_summary(summary, console: CliConsole, project_dir: Path | None = None) -> None:
    rows = [
        ("Report", str(summary.report_path) if summary.report_path else "not rendered"),
        ("Successful PDFs", summary.successful_pdfs),
        ("Failed PDFs", summary.failed_pdfs),
        ("Extracted effects", summary.extracted_effects),
        ("Usable z-curve inputs", summary.usable_zcurve_inputs),
        ("Input tokens", summary.input_tokens),
        ("Output tokens", summary.output_tokens),
        ("Total tokens", summary.total_tokens),
    ]
    console.table("Run Summary", ["Metric", "Value"], rows)
    if project_dir is not None:
        rendered = format_run_result(summary, project_dir)
        if "Z-Curve Summary:" in rendered:
            console.print("Z-Curve Summary")
            console.print(rendered.split("Z-Curve Summary:", 1)[1].strip())


def offer_interactive_retry(
    args: argparse.Namespace,
    project_dir: Path,
    settings: RunSettings,
    console: CliConsole,
    api_key: str,
):
    failures = [
        record
        for record in load_extractions(project_dir)
        if record.get("status") != "ok"
    ]
    if not failures:
        return None

    console.table(
        "Failed PDFs",
        ["Source", "Error"],
        [
            (
                record.get("source_name", ""),
                classify_error(str(record.get("error") or "")).compact(),
            )
            for record in failures
        ],
    )

    q = _questionary()
    if q:
        action = q.select(
            "Retry failed files now?",
            choices=[
                {"name": "Retry all failed files", "value": "all"},
                {"name": "Choose failed files", "value": "selected"},
                {"name": "Do not retry", "value": "no"},
            ],
            default="no",
        ).ask()
        if action == "no" or action is None:
            return None
        selected_sources = None
        if action == "selected":
            selected_sources = q.checkbox(
                "Select files to retry:",
                choices=[str(record.get("source_name", "")) for record in failures],
            ).ask()
            if not selected_sources:
                return None
    else:
        answer = input("Retry all failed files now? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            return None
        selected_sources = None

    return retry_project(
        project_dir=project_dir,
        settings=settings,
        selected_sources=selected_sources,
        assume_yes=True,
        skip_report=bool(args.skip_report),
        console=console,
        api_key=api_key,
    )


def main(argv: list[str] | None = None) -> int:
    console = CliConsole()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            try:
                from .tui import run_tui

                return run_tui()
            except RuntimeError as exc:
                console.warn(str(exc))
                return guided(console)
        if args.command == "run":
            return run_command(args, args.project_dir, interactive=False, console=console)
        if args.command == "retry":
            return retry_command(args, console)
        if args.command == "gui":
            from .tui import run_tui

            return run_tui()
        parser.print_help()
        return 2
    except PreflightError as exc:
        console.error(classify_error(exc).compact())
        return 2
    except KeyboardInterrupt:
        console.warn("Cancelled.")
        return 130
    except Exception as exc:
        console.error(classify_error(exc).compact())
        return 1


if __name__ == "__main__":
    sys.exit(main())
