from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .artifacts import load_extractions
from .config import DEFAULTS, RunSettings, load_app_settings, load_run_settings
from .console import CliConsole
from .models import fallback_models, list_live_models, model_request_defaults, normalize_model_name, validate_model_option
from .preflight import PreflightError, run_preflight
from .runner import ensure_project_layout, retry_project, run_project
from .env import resolve_api_key
from .user_facing import classify_error, format_run_result
from .providers import normalize_provider, provider_label


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


def prompt_api_key(
    project_dir: Path,
    explicit_key: str | None,
    interactive: bool,
    provider: str = "gemini",
) -> str:
    selected = normalize_provider(provider)
    try:
        return resolve_api_key(project_dir, explicit_key=explicit_key, provider=selected)
    except RuntimeError:
        if not interactive:
            raise

    q = _questionary()
    if q:
        answer = q.password(f"{provider_label(selected)} API key:").ask()
    else:
        import getpass

        answer = getpass.getpass(f"{provider_label(selected)} API key: ")

    return resolve_api_key(project_dir, explicit_key=answer, provider=selected)


def prompt_model(api_key: str, console: CliConsole, provider: str = "gemini") -> str:
    selected_provider = normalize_provider(provider)
    if selected_provider == "openrouter":
        primary = input("OpenRouter model ID (for example vendor/model): ").strip()
        return validate_model_option("openrouter", primary, api_key).name
    try:
        options = list_live_models(api_key, selected_provider)
        if not options:
            raise RuntimeError(f"No compatible {provider_label(selected_provider)} models were returned.")
    except Exception as exc:
        console.warn(f"Live model discovery was unavailable: {exc}")
        options = fallback_models(selected_provider)

    choice_names = [option.name for option in options]
    q = _questionary()
    if q:
        primary = q.select("Primary extraction model:", choices=choice_names).ask()
        return normalize_model_name(primary, selected_provider)

    console.table("Available Models", ["#", "Model"], enumerate(choice_names, start=1))
    selected = int(input("Primary extraction model number: ").strip())
    primary = choice_names[selected - 1]
    return normalize_model_name(primary, selected_provider)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auto-zcurve")
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Run extraction for a project directory.")
    run.add_argument("project_dir", type=Path)
    run.add_argument("--yes", action="store_true", help="Accept setup defaults and do not prompt.")
    run.add_argument("--provider", choices=("gemini", "openrouter"), help="LLM provider (default: saved setting or gemini).")
    run.add_argument("--model", help="Primary provider model ID.")
    run.add_argument("--api-key", help="Selected provider's API key for this run. Not stored.")
    run.add_argument("--parallel", type=int, help="Number of PDFs to process at the same time.")
    run.add_argument("--force", action="store_true", help="Reprocess PDFs even if they already succeeded.")
    run.add_argument("--skip-preflight", action="store_true", help=argparse.SUPPRESS)
    run.add_argument("--skip-report", action="store_true", help="Skip Quarto report rendering.")

    retry = subparsers.add_parser("retry", help="Retry failed files from the latest project outputs.")
    retry.add_argument("project_dir", type=Path)
    retry.add_argument("--yes", action="store_true", help="Retry all failed files without prompting.")
    retry.add_argument("--provider", choices=("gemini", "openrouter"), help="Override the saved LLM provider.")
    retry.add_argument("--model", help="Override the saved provider model.")
    retry.add_argument("--api-key", help="Selected provider's API key for this retry. Not stored.")
    retry.add_argument("--parallel", type=int, help="Number of PDFs to retry at the same time.")
    retry.add_argument("--source", action="append", help="Retry only this source path relative to sources/.")
    retry.add_argument("--skip-preflight", action="store_true", help=argparse.SUPPRESS)
    retry.add_argument("--skip-report", action="store_true", help="Skip Quarto report rendering.")

    web = subparsers.add_parser("web", help="Launch the local browser app.")
    web.add_argument("--no-browser", action="store_true", help="Print the local URL without opening a browser.")
    web.add_argument("--port", type=int, help=argparse.SUPPRESS)

    subparsers.add_parser("tui", help="Launch the legacy Textual terminal interface.")
    subparsers.add_parser("gui", help=argparse.SUPPRESS)
    return parser


def _settings_from_args(
    args: argparse.Namespace,
    project_dir: Path,
    console: CliConsole,
    interactive: bool,
    api_key: str,
) -> RunSettings:
    existing = load_run_settings(project_dir)
    app_defaults = load_app_settings()
    provider = normalize_provider(args.provider or (existing.provider if existing else "gemini"))
    primary = args.model or (
        existing.primary_model if existing and existing.provider == provider else None
    )

    if not primary and not interactive:
        raise RuntimeError(
            f"A {provider_label(provider)} model is required. Pass --model or use the browser app."
        )

    if not primary:
        primary = prompt_model(api_key, console, provider)

    if provider == "openrouter":
        primary = validate_model_option(provider, primary, api_key).name

    model_parallel, model_delay = model_request_defaults(primary, provider)

    return RunSettings(
        primary_model=normalize_model_name(primary, provider),
        provider=provider,
        request_timeout_sec=(
            existing.request_timeout_sec if existing else app_defaults.request_timeout_sec
        ),
        parallel_requests=max(
            1,
            int(args.parallel if args.parallel is not None else (existing.parallel_requests if existing else model_parallel)),
        ),
        request_delay_sec=existing.request_delay_sec if existing else model_delay,
        max_upload_size_mb=(
            existing.max_upload_size_mb if existing else app_defaults.max_upload_size_mb
        ),
        effect_definition=existing.effect_definition if existing else None,
        pdf_parser=app_defaults.pdf_parser,
        reasoning_effort=app_defaults.reasoning_effort,
        service_tier=app_defaults.service_tier,
    )


def guided(console: CliConsole) -> int:
    console.title("Guided PDF extraction and z-curve reporting")
    project_dir = prompt_project_dir()
    args = argparse.Namespace(
        model=None,
        provider=None,
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
    existing = load_run_settings(project_dir)
    provider = normalize_provider(args.provider or (existing.provider if existing else "gemini"))
    api_key = prompt_api_key(
        project_dir,
        explicit_key=args.api_key,
        interactive=prompt_allowed,
        provider=provider,
    )
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
    existing = load_run_settings(project_dir)
    provider = normalize_provider(args.provider or (existing.provider if existing else "gemini"))
    api_key = prompt_api_key(
        project_dir,
        explicit_key=args.api_key,
        interactive=False,
        provider=provider,
    )
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
            from .web import launch_web

            return launch_web()
        if args.command == "web":
            from .web import launch_web

            return launch_web(open_browser=not args.no_browser, port=args.port)
        if args.command == "run":
            return run_command(args, args.project_dir, interactive=False, console=console)
        if args.command == "retry":
            return retry_command(args, console)
        if args.command in {"tui", "gui"}:
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
