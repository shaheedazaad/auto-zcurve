from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .artifacts import load_extractions
from .config import RunSettings, load_app_settings, save_selected_extractor_id
from .console import CliConsole
from .credentials import KeyVault, VaultUnavailable
from .extractors import (
    Extractor,
    find_extractor_by_name,
    legacy_claims,
    load_extractors,
    project_extractor,
    record_legacy_adoption,
    run_settings_for,
)
from .preflight import PreflightError, run_preflight
from .runner import ensure_project_layout, retry_project, run_project
from .user_facing import classify_error, format_run_result, needs_review_pdf_rows, retry_reason


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


def _extractor_names() -> str:
    names = [item.name for item in load_extractors()]
    return ", ".join(f"“{name}”" for name in names) if names else "none yet"


def prompt_extractor(console: CliConsole) -> Extractor | None:
    extractors = load_extractors()
    if not extractors:
        return None
    q = _questionary()
    if q:
        chosen = q.select(
            "Extractor:",
            choices=[{"name": f"{item.name} ({item.provider_label} · {item.model})", "value": item.id} for item in extractors],
        ).ask()
        return next((item for item in extractors if item.id == chosen), None)
    console.table("Extractors", ["#", "Name", "Model"], [(index, item.name, item.model) for index, item in enumerate(extractors, start=1)])
    answer = input("Extractor number: ").strip()
    return extractors[int(answer) - 1] if answer.isdigit() and 0 < int(answer) <= len(extractors) else None


def choose_extractor(name: str | None, project_dir: Path, interactive: bool, console: CliConsole) -> Extractor:
    """The extractor to run with: named with --extractor, else the project's, else asked for."""
    if name:
        extractor = find_extractor_by_name(name)
        if extractor is None:
            raise RuntimeError(f"No extractor is called “{name}”. Available extractors: {_extractor_names()}.")
    else:
        extractor = project_extractor(project_dir).extractor
        if extractor is None and interactive:
            extractor = prompt_extractor(console)
        if extractor is None:
            if not load_extractors():
                raise RuntimeError(
                    "No extractors exist yet. Create one in the browser app (run `auto-zcurve`) under Settings."
                )
            raise RuntimeError(f"Choose an extractor with --extractor NAME. Available extractors: {_extractor_names()}.")
    save_selected_extractor_id(project_dir, extractor.id)
    return extractor


def extractor_key(extractor: Extractor) -> str:
    """The extractor's saved API key; "" for a keyless OpenAI-compatible server."""
    key = None
    if extractor.key_saved or extractor.legacy_credential:
        vault = KeyVault(legacy_claims=legacy_claims, on_legacy_adopted=record_legacy_adoption)
        try:
            key = vault.get(extractor.id)
        except VaultUnavailable as exc:
            raise RuntimeError(str(exc)) from exc
    if extractor.requires_key and not key:
        raise RuntimeError(
            f"The “{extractor.name}” extractor has no API key. Add one in the browser app under Settings."
        )
    return key or ""


def list_extractors_command(console: CliConsole) -> int:
    extractors = load_extractors()
    if not extractors:
        console.info("No extractors yet. Create one in the browser app (run `auto-zcurve`) under Settings.")
        return 0
    console.table(
        "Extractors",
        ["Name", "Provider", "Model", "API key"],
        [
            (item.name, item.provider_label, item.model or "not set",
             "saved" if item.key_saved or item.legacy_credential else ("not set" if item.requires_key else "not set (optional)"))
            for item in extractors
        ],
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auto-zcurve")
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Run extraction for a project directory.")
    run.add_argument("project_dir", type=Path)
    run.add_argument("--yes", action="store_true", help="Accept setup defaults and do not prompt.")
    run.add_argument("--extractor", metavar="NAME", help="Extractor to run with (default: the project's extractor).")
    run.add_argument("--force", action="store_true", help="Reprocess articles even if they already succeeded.")
    run.add_argument("--skip-preflight", action="store_true", help=argparse.SUPPRESS)
    run.add_argument("--skip-report", action="store_true", help="Skip Quarto report rendering.")

    retry = subparsers.add_parser("retry", help="Retry failed and needs-review files from the latest project outputs.")
    retry.add_argument("project_dir", type=Path)
    retry.add_argument("--yes", action="store_true", help="Retry all failed and needs-review files without prompting.")
    retry.add_argument("--extractor", metavar="NAME", help="Extractor to retry with (default: the project's extractor).")
    retry.add_argument("--source", action="append", help="Retry only this source path relative to sources/.")
    retry.add_argument("--skip-preflight", action="store_true", help=argparse.SUPPRESS)
    retry.add_argument("--skip-report", action="store_true", help="Skip Quarto report rendering.")

    subparsers.add_parser("extractors", help="List the extractors created in the browser app.")

    web = subparsers.add_parser("web", help="Launch the local browser app.")
    web.add_argument("--no-browser", action="store_true", help="Print the local URL without opening a browser.")
    web.add_argument("--port", type=int, help=argparse.SUPPRESS)
    return parser


def _run_inputs(
    args: argparse.Namespace,
    project_dir: Path,
    interactive: bool,
    console: CliConsole,
) -> tuple[RunSettings, str]:
    extractor = choose_extractor(args.extractor, project_dir, interactive, console)
    api_key = extractor_key(extractor)
    console.secrets.append(api_key)
    return run_settings_for(extractor, load_app_settings()), api_key


def guided(console: CliConsole) -> int:
    console.title("Guided article extraction and z-curve reporting")
    project_dir = prompt_project_dir()
    args = argparse.Namespace(
        extractor=None,
        yes=False,
        force=False,
        skip_preflight=False,
        skip_report=False,
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
    settings, api_key = _run_inputs(args, project_dir, interactive and not args.yes, console)
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
    if interactive and (summary.failed_pdfs or summary.needs_review_pdfs):
        retry_summary = offer_interactive_retry(args, project_dir, settings, console, api_key)
        if retry_summary:
            print_summary(retry_summary, console, project_dir)
            summary = retry_summary
    return 0 if summary.failed_pdfs == 0 else 1


def retry_command(args: argparse.Namespace, console: CliConsole) -> int:
    project_dir = args.project_dir.expanduser().resolve()
    if not args.skip_preflight:
        run_preflight(project_dir, interactive=False, console=console)
    settings, api_key = _run_inputs(args, project_dir, False, console)
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
        ("Successful articles", summary.successful_pdfs),
        ("Needs review articles", summary.needs_review_pdfs),
        ("Failed articles", summary.failed_pdfs),
        ("Extracted effects", summary.extracted_effects),
        ("Usable z-curve inputs", summary.usable_zcurve_inputs),
        ("Input tokens", summary.input_tokens),
        ("Output tokens", summary.output_tokens),
        ("Total tokens", summary.total_tokens),
    ]
    console.table("Run Summary", ["Metric", "Value"], rows)
    if project_dir is not None:
        review_rows = needs_review_pdf_rows(project_dir)
        if review_rows:
            console.table("Articles Needing Review", ["Source", "Reasons"], review_rows)
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
        "Failed and Needs-Review Articles",
        ["Source", "Problem"],
        [(record.get("source_name", ""), retry_reason(record)) for record in failures],
    )

    q = _questionary()
    if q:
        action = q.select(
            "Retry failed and needs-review files now?",
            choices=[
                {"name": "Retry all failed and needs-review files", "value": "all"},
                {"name": "Choose files to retry", "value": "selected"},
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
        answer = input("Retry all failed and needs-review files now? [y/N] ").strip().lower()
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
        if args.command == "extractors":
            return list_extractors_command(console)
        if args.command == "run":
            return run_command(args, args.project_dir, interactive=False, console=console)
        return retry_command(args, console)
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
