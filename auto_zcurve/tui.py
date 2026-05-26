from __future__ import annotations

import os
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from typing import Iterable, Iterator

from .artifacts import read_zcurve_summary
from .config import DEFAULTS, RunSettings, load_run_settings
from .credentials import delete_saved_api_key, load_last_project_dir, load_saved_api_key, save_api_key, save_last_project_dir
from .models import fallback_models, normalize_model_name
from .paths import DEFAULT_SCHEMA
from .preflight import run_preflight
from .runner import retry_project, run_project
from .user_facing import (
    check_project_readiness,
    classify_error,
    failed_pdf_rows,
    open_report_path,
)


MIN_TUI_COLUMNS = 124
MIN_TUI_ROWS = 48
TERMINAL_RESIZE_OPT_OUT = "AUTO_ZCURVE_NO_TERMINAL_RESIZE"


def article_summary(project_dir: Path) -> tuple[int, str]:
    sources_dir = project_dir / "sources"
    if not project_dir.exists():
        return 0, "Project directory does not exist yet."
    if not sources_dir.exists():
        return 0, "No sources/ folder found. Choose a project folder that already contains sources/."

    pdfs = sorted(path for path in sources_dir.rglob("*.pdf") if path.is_file())
    label = "article" if len(pdfs) == 1 else "articles"
    return len(pdfs), f"{len(pdfs)} PDF {label} found in sources/."


def is_hidden_path(path: Path | str) -> bool:
    return Path(path).name.startswith(".")


def display_path(path: Path | str, base: Path | str | None = None) -> str:
    resolved = Path(path).expanduser().resolve()

    if base is not None:
        try:
            return str(resolved.relative_to(Path(base).expanduser().resolve()))
        except ValueError:
            pass

    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        pass

    try:
        home = Path.home().resolve()
        relative_to_home = resolved.relative_to(home)
        return str(Path("~") / relative_to_home)
    except ValueError:
        return str(resolved)


def terminal_resize_sequence(columns: int, rows: int) -> str:
    return f"\033[8;{rows};{columns}t"


def requested_terminal_size(
    current_columns: int,
    current_rows: int,
    *,
    min_columns: int = MIN_TUI_COLUMNS,
    min_rows: int = MIN_TUI_ROWS,
) -> tuple[int, int] | None:
    target_columns = max(current_columns, min_columns)
    target_rows = max(current_rows, min_rows)
    if target_columns == current_columns and target_rows == current_rows:
        return None
    return target_columns, target_rows


def request_terminal_resize(
    *,
    min_columns: int = MIN_TUI_COLUMNS,
    min_rows: int = MIN_TUI_ROWS,
    stream=None,
) -> bool:
    if os.environ.get(TERMINAL_RESIZE_OPT_OUT):
        return False

    close_output = False
    output = stream or sys.stdout
    if stream is None and not getattr(output, "isatty", lambda: False)() and os.name != "nt":
        try:
            output = open("/dev/tty", "w", encoding="utf-8")
            close_output = True
        except OSError:
            output = sys.stdout

    if not getattr(output, "isatty", lambda: False)():
        if close_output:
            output.close()
        return False

    try:
        size = os.get_terminal_size(output.fileno())
    except (AttributeError, OSError):
        size = shutil.get_terminal_size(fallback=(min_columns, min_rows))
    target = requested_terminal_size(
        size.columns,
        size.lines,
        min_columns=min_columns,
        min_rows=min_rows,
    )
    if target is None:
        return False

    columns, rows = target
    try:
        output.write(terminal_resize_sequence(columns, rows))
        output.flush()
    finally:
        if close_output:
            output.close()
    time.sleep(0.05)
    return True


def run_tui() -> int:
    request_terminal_resize()
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.screen import ModalScreen
        from textual.widgets import Button, Collapsible, DirectoryTree, Footer, Header, Input, ProgressBar, RichLog, Select, Static
        from rich.markup import escape
    except ImportError as exc:
        raise RuntimeError("Textual is not installed. Run `pip install -e .` to install GUI dependencies.") from exc

    class TextualConsole:
        def __init__(self, app: "AutoZCurveApp") -> None:
            self.app = app

        def _write(self, message: str) -> None:
            self.app.write_log(message)

        def _section(self, title: str) -> None:
            self._write("")
            self._write(f"[bold #bb9af7]{escape(title)}[/bold #bb9af7]")

        def print(self, *parts: object) -> None:
            self._write(escape(" ".join(str(part) for part in parts)))

        def title(self, text: str, subtitle: str | None = None) -> None:
            self._write(f"[bold]{escape(text)}[/bold]")
            if subtitle:
                self._write(f"[dim]{escape(subtitle)}[/dim]")

        def info(self, text: str) -> None:
            self._write(f"[cyan]{escape(text)}[/cyan]")

        def warn(self, text: str) -> None:
            self._write(f"[yellow]▲ {escape(text)}[/yellow]")

        def error(self, text: str) -> None:
            self._write(f"[bold red]✗ {escape(text)}[/bold red]")

        def success(self, text: str) -> None:
            self._write(f"[green]✓ {escape(text)}[/green]")

        def highlight(self, text: str) -> None:
            self._write(f"[#7dcfff]{escape(text)}[/#7dcfff]")

        def table(self, title: str, columns: Iterable[str], rows: Iterable[Iterable[object]]) -> None:
            cols = list(columns)
            data = list(rows)
            self._section(title)
            if len(cols) == 2:
                # key-value layout: right-pad the key column
                key_width = max((len(str(r[0])) for r in data), default=0)
                for row in data:
                    key = str(row[0]).ljust(key_width)
                    val = str(row[1])
                    self._write(f"  [dim]{escape(key)}[/dim]  {escape(val)}")
            else:
                self._write("  " + "  ".join(f"[dim]{escape(c)}[/dim]" for c in cols))
                for row in data:
                    self._write("  " + "  ".join(escape(str(item)) for item in row))

        @contextmanager
        def progress(self, total: int, description: str) -> Iterator["_TextualProgress"]:
            self._section(description)
            self.app.reset_progress(total, description)
            yield _TextualProgress(self, total)

    class _TextualProgress:
        def __init__(self, console: TextualConsole, total: int) -> None:
            self.console = console
            self.total = total
            self.done = 0

        def advance(self, description: str | None = None) -> None:
            self.done += 1
            label = description or "Processed"
            status, sep, name = label.partition(": ")
            if sep:
                color = "green" if status == "ok" else "red"
                msg = f"  [{color}]{escape(status)}[/{color}] [dim]{escape(name)} ({self.done}/{self.total})[/dim]"
            else:
                msg = f"  [dim]{escape(label)} ({self.done}/{self.total})[/dim]"
            self.console._write(msg)
            self.console.app.advance_progress(label, self.done, self.total)

    class DirectoryPicker(ModalScreen):
        CSS = """
        DirectoryPicker {
            align: center middle;
        }

        #picker {
            width: 80%;
            height: 80%;
            border: solid $primary;
            background: $surface;
            padding: 1 2;
        }

        #picker_controls {
            height: auto;
        }

        #tree_container {
            height: 1fr;
            border: solid $panel;
            margin: 0 0 1 0;
            padding: 0;
        }

        #parent_directory {
            width: 100%;
            height: 1;
            min-height: 1;
            padding: 0 1;
            background: $boost;
            color: $text-muted;
            text-style: bold;
            content-align: left middle;
            border: none;
        }

        #parent_directory:hover,
        #parent_directory:focus,
        #parent_directory:focus:hover {
            color: $accent;
            background: $panel;
            text-style: bold;
        }

        #directory_tree {
            height: 1fr;
        }

        #selected_directory {
            color: $text-muted;
            margin-bottom: 1;
        }

        #picker_note {
            color: $accent;
            margin-bottom: 1;
        }
        """

        BINDINGS = [("escape", "cancel", "Cancel")]

        def __init__(self, start_path: Path) -> None:
            super().__init__()
            self.selected_path = start_path

        def compose(self) -> ComposeResult:
            with Vertical(id="picker"):
                yield Static("Select project directory", classes="section-title")
                yield Static(display_path(self.selected_path), id="selected_directory")
                yield Static("Choose a folder that contains a sources/ subdirectory with PDF articles.", id="picker_note")
                with Horizontal(id="picker_controls"):
                    yield Button("Use selected directory", id="use_directory", variant="primary", flat=True)
                    yield Button("Cancel", id="cancel_directory", flat=True)
                with Vertical(id="tree_container"):
                    yield Button("↑ Parent directory", id="parent_directory", flat=True)
                    yield VisibleDirectoryTree(str(self.selected_path), id="directory_tree")

        def action_cancel(self) -> None:
            self.dismiss(None)

        def on_directory_tree_directory_selected(self, event) -> None:
            self.selected_path = Path(event.path).expanduser().resolve()
            self.query_one("#selected_directory", Static).update(display_path(self.selected_path))

        def on_directory_tree_file_selected(self, event) -> None:
            candidate = Path(event.path).expanduser().resolve()
            if candidate.parent.exists():
                self.selected_path = candidate.parent
                self.query_one("#selected_directory", Static).update(display_path(self.selected_path))

        def on_tree_node_selected(self, event) -> None:
            data = getattr(event.node, "data", None)
            path = getattr(data, "path", data)
            if path:
                candidate = Path(path)
                if candidate.is_dir():
                    self.selected_path = candidate.expanduser().resolve()
                    self.query_one("#selected_directory", Static).update(display_path(self.selected_path))

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "parent_directory":
                self.go_up()
            elif event.button.id == "use_directory":
                self.dismiss(self.selected_path)
            elif event.button.id == "cancel_directory":
                self.dismiss(None)

        def go_up(self) -> None:
            parent = self.selected_path.parent
            if parent == self.selected_path:
                return
            self.selected_path = parent
            self.query_one("#selected_directory", Static).update(display_path(self.selected_path))
            tree = self.query_one("#directory_tree", DirectoryTree)
            try:
                tree.path = str(self.selected_path)
                tree.reload()
            except Exception as exc:
                self.query_one("#selected_directory", Static).update(
                    f"{display_path(self.selected_path)} (refresh failed: {exc})"
                )

    class VisibleDirectoryTree(DirectoryTree):
        def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
            return [path for path in paths if not is_hidden_path(path)]

    class DefaultSchemaModal(ModalScreen):
        CSS = """
        DefaultSchemaModal {
            align: center middle;
        }

        #schema_notice {
            width: 68;
            max-width: 90%;
            border: solid $primary;
            background: $surface;
            padding: 1 2;
        }

        #schema_notice_body {
            margin: 1 0;
            color: $text;
        }
        """

        BINDINGS = [("escape", "cancel", "Cancel")]

        def __init__(self, schema_path: Path, project_dir: Path) -> None:
            super().__init__()
            self.schema_path = schema_path
            self.project_dir = project_dir

        def compose(self) -> ComposeResult:
            body = (
                "No extraction_schema.yml file was found for this project.\n\n"
                "The default schema extracts focal statistics only: statistics reported in the article title "
                "or abstract.\n\n"
                "Choose Use Default to create the schema and continue. Choose Cancel to return and customize "
                "the schema yourself at:\n"
                f"{display_path(self.schema_path, base=self.project_dir)}"
            )
            with Vertical(id="schema_notice"):
                yield Static("Default Extraction Schema", classes="section-title")
                yield Static(body, id="schema_notice_body")
                with Horizontal(classes="button-row"):
                    yield Button("Use Default", id="use_default_schema", variant="primary", flat=True)
                    yield Button("Cancel", id="cancel_schema_notice", flat=True)

        def action_cancel(self) -> None:
            self.dismiss(False)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "use_default_schema":
                self.dismiss(True)
            elif event.button.id == "cancel_schema_notice":
                self.dismiss(False)

    class AutoZCurveApp(App):
        CSS = """
        Screen {
            layout: vertical;
        }

        #body {
            padding: 0 1;
            height: 1fr;
        }

        #title {
            text-style: bold;
            color: $primary;
            margin: 0 0 0 1;
            height: 1;
        }

        #workspace {
            height: 1fr;
        }

        #left_pane {
            width: 44;
            min-width: 34;
            height: 1fr;
            overflow-y: auto;
        }

        #right_pane {
            width: 1fr;
            height: 1fr;
        }

        .panel {
            border: tall $panel;
            padding: 0 1 0 1;
            margin: 0 1 1 0;
            background: $boost;
            height: auto;
        }

        .section-title {
            text-style: bold;
            color: $secondary;
            height: 1;
            margin: 0;
        }

        .field-label {
            color: $text-muted;
            height: 1;
            margin: 1 0 0 0;
        }

        #project_row {
            height: auto;
        }

        #project_dir {
            width: 1fr;
        }

        #browse_project {
            width: 8;
            min-width: 8;
        }

        Input, Select {
            margin: 0;
            height: 3;
        }

        .button-row {
            height: auto;
            margin: 1 0 0 0;
        }

        Button {
            margin: 0 1 0 0;
            height: 3;
            min-width: 8;
        }

        #run,
        #retry,
        #open_report,
        #save_key,
        #delete_key {
            width: 1fr;
            min-width: 8;
        }

        #status {
            color: $text;
            min-height: 1;
            margin: 1 0 0 0;
        }

        #article_summary {
            color: $accent;
            min-height: 1;
            margin: 0;
        }

        #progress {
            margin: 1 0 0 0;
        }

        #progress_status {
            color: $text-muted;
            min-height: 1;
            margin: 0;
        }

        Collapsible {
            margin: 0 1 1 0;
            background: $boost;
            border: tall $panel;
            padding: 0 1 0 1;
        }

        CollapsibleTitle {
            color: $text-muted;
            padding: 0;
        }

        #log {
            height: 1fr;
            border: tall $primary;
            background: $boost;
            padding: 0 1;
        }
        """

        BINDINGS = [("q", "quit", "Quit")]

        def on_mount(self) -> None:
            self.theme = "tokyo-night"
            self.busy = False
            self.last_report_path: Path | None = None
            self.log_widget = self.query_one("#log", RichLog)
            self.status_widget = self.query_one("#status", Static)
            self.article_widget = self.query_one("#article_summary", Static)
            self.progress_status_widget = self.query_one("#progress_status", Static)
            self.progress_widget = self.query_one("#progress", ProgressBar)
            self.refresh_readiness()

        def compose(self) -> ComposeResult:
            saved_key = load_saved_api_key()
            model_options = [(model.name, model.name) for model in fallback_models()]
            initial_project_dir = load_last_project_dir() or Path.cwd()

            yield Header(show_clock=True)
            with Vertical(id="body"):
                yield Static("Auto Z-Curve", id="title")
                with Horizontal(id="workspace"):
                    with Vertical(id="left_pane"):
                        with Vertical(id="setup", classes="panel"):
                            yield Static("Setup", classes="section-title")
                            yield Static("Project folder", classes="field-label")
                            with Horizontal(id="project_row"):
                                yield Input(placeholder="Project directory", value=display_path(initial_project_dir), id="project_dir")
                                yield Button("Pick", id="browse_project", variant="primary", flat=True)
                            yield Static("", id="article_summary")
                            yield Static("Gemini API key", classes="field-label")
                            yield Input(
                                placeholder="Paste key here",
                                password=True,
                                value=saved_key or "",
                                id="api_key",
                            )
                            with Horizontal(classes="button-row"):
                                yield Button("Save key", id="save_key", variant="primary", flat=True)
                                yield Button("Delete", id="delete_key", variant="warning", flat=True)
                            yield Static("Model", classes="field-label")
                            yield Select(model_options, value=model_options[0][1], prompt="Model", id="model")
                        with Vertical(id="run_panel", classes="panel"):
                            yield Static("Run", classes="section-title")
                            yield ProgressBar(total=1, id="progress")
                            yield Static("Waiting", id="progress_status")
                            with Horizontal(classes="button-row"):
                                yield Button("Run", id="run", variant="success", flat=True)
                                yield Button("Retry", id="retry", variant="default", flat=True)
                            yield Button("Open Report", id="open_report", variant="primary", flat=True, disabled=True)
                            yield Static(self._key_status(saved_key), id="status")
                        with Collapsible(title="Advanced", id="advanced", collapsed=True):
                            yield Static("Parallel PDFs", classes="field-label")
                            yield Input(
                                placeholder="Parallel PDFs",
                                value=str(DEFAULTS["parallel_requests"]),
                                id="parallel_requests",
                            )
                            yield Static("Timeout (sec)", classes="field-label")
                            yield Input(
                                placeholder="Timeout (sec)",
                                value=str(DEFAULTS["request_timeout_sec"]),
                                id="timeout_sec",
                            )
                            yield Static("Max upload (MB)", classes="field-label")
                            yield Input(
                                placeholder="Max upload (MB)",
                                value=str(DEFAULTS["max_upload_size_mb"]),
                                id="max_upload_mb",
                            )
                    with Vertical(id="right_pane"):
                        yield RichLog(id="log", wrap=True, markup=True)
            yield Footer()

        def write_log(self, message: str) -> None:
            self.call_from_thread(self.log_widget.write, message)

        def update_status(self, message: str) -> None:
            self.status_widget.update(message)

        def feedback(self, message: str, severity: str = "information") -> None:
            self.update_status(message)
            self.log_widget.write(message)
            try:
                self.notify(message, severity=severity)
            except Exception:
                pass

        def reset_progress(self, total: int, label: str) -> None:
            self.call_from_thread(self._reset_progress, total, label)

        def _reset_progress(self, total: int, label: str) -> None:
            self.progress_widget.update(total=max(total, 1), progress=0)
            self.progress_status_widget.update(f"{label}: 0 / {max(total, 1)}")
            self.update_status(label)

        def advance_progress(self, label: str, done: int, total: int) -> None:
            self.call_from_thread(self._advance_progress, label, done, total)

        def _advance_progress(self, label: str, done: int, total: int) -> None:
            self.progress_widget.advance(1)
            self.progress_status_widget.update(f"Gemini processing: {done} / {total}")
            self.update_status(label)

        def _key_status(self, saved_key: str | None) -> str:
            if saved_key:
                return "Saved API key found. You can replace it or delete it."
            return "No saved API key. Enter one for this session, or save it permanently."

        def on_button_pressed(self, event: Button.Pressed) -> None:
            button_id = event.button.id
            if button_id == "save_key":
                self.save_key()
            elif button_id == "delete_key":
                self.delete_key()
            elif button_id == "browse_project":
                self.open_project_picker()
            elif button_id == "run":
                self.start_run(retry=False)
            elif button_id == "retry":
                self.start_run(retry=True)
            elif button_id == "open_report":
                self.open_report()

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id in {"project_dir", "api_key"}:
                self.refresh_readiness()

        def on_select_changed(self, event: Select.Changed) -> None:
            if event.select.id == "model":
                self.refresh_readiness()

        def open_project_picker(self) -> None:
            current = Path(self.query_one("#project_dir", Input).value or ".").expanduser()
            if not current.exists():
                current = Path.cwd()
            self.push_screen(DirectoryPicker(current.resolve()), self.set_project_directory)

        def set_project_directory(self, selected: Path | None) -> None:
            if selected is None:
                self.update_status("Directory selection cancelled.")
                return
            self.query_one("#project_dir", Input).value = display_path(selected)
            self.update_status(f"Selected project directory: {display_path(selected)}")
            save_last_project_dir(selected)
            self.refresh_readiness()

        def refresh_article_summary(self) -> None:
            project_dir = Path(self.query_one("#project_dir", Input).value or ".").expanduser().resolve()
            count, message = article_summary(project_dir)
            self.article_widget.update(message)
            if count:
                self.update_status(message)

        def refresh_readiness(self) -> None:
            project_dir = Path(self.query_one("#project_dir", Input).value or ".").expanduser().resolve()
            api_key = self.query_one("#api_key", Input).value.strip()
            model = self.query_one("#model", Select).value
            _count, message = article_summary(project_dir)
            readiness = check_project_readiness(project_dir, api_key=api_key, model=str(model or ""))
            self.article_widget.update(message)
            if self.busy:
                return
            try:
                has_failures = bool(failed_pdf_rows(project_dir, limit=1))
            except Exception:
                has_failures = False
            self.query_one("#run", Button).disabled = not readiness.ready
            self.query_one("#retry", Button).disabled = not readiness.ready or not has_failures
            self.query_one("#open_report", Button).disabled = not self._current_report_path(project_dir).exists()
            self.update_status(readiness.next_action)

        def set_busy(self, busy: bool) -> None:
            self.busy = busy
            for button_id in ("run", "retry", "open_report", "save_key", "delete_key", "browse_project"):
                self.query_one(f"#{button_id}", Button).disabled = busy
            if not busy:
                self.refresh_readiness()

        def save_key(self) -> None:
            api_key = self.query_one("#api_key", Input).value.strip()
            try:
                path = save_api_key(api_key)
            except ValueError as exc:
                self.feedback(str(exc), severity="error")
                return
            self.feedback(f"Saved API key to {display_path(path)}.", severity="information")
            self.refresh_readiness()

        def delete_key(self) -> None:
            deleted = delete_saved_api_key()
            self.query_one("#api_key", Input).value = ""
            self.feedback("Deleted saved API key." if deleted else "No saved API key to delete.")
            self.refresh_readiness()

        def _current_report_path(self, project_dir: Path | None = None) -> Path:
            current_project = project_dir or Path(self.query_one("#project_dir", Input).value or ".").expanduser().resolve()
            if self.last_report_path is not None:
                try:
                    self.last_report_path.resolve().relative_to(current_project.resolve())
                    return self.last_report_path
                except ValueError:
                    pass
            return current_project / "output" / "report.html"

        def open_report(self) -> None:
            project_dir = Path(self.query_one("#project_dir", Input).value or ".").expanduser().resolve()
            report_path = self._current_report_path(project_dir)
            try:
                open_report_path(report_path)
            except Exception as exc:
                summary = classify_error(exc)
                self.feedback(
                    f"{summary.explanation} Report path: {display_path(report_path, base=project_dir)}",
                    severity="error",
                )
                self.log_widget.write(summary.technical_detail or str(exc))
                return
            self.feedback(f"Opened report: {display_path(report_path, base=project_dir)}")

        def start_run(self, retry: bool) -> None:
            project_dir = Path(self.query_one("#project_dir", Input).value).expanduser().resolve()
            api_key = self.query_one("#api_key", Input).value.strip() or load_saved_api_key()
            model = self.query_one("#model", Select).value
            try:
                parallel_requests = max(1, int(self.query_one("#parallel_requests", Input).value.strip() or "1"))
            except ValueError:
                self.update_status("Enter a whole number for Parallel PDFs.")
                return
            try:
                timeout_sec = max(1, int(self.query_one("#timeout_sec", Input).value.strip() or str(DEFAULTS["request_timeout_sec"])))
            except ValueError:
                self.update_status("Enter a whole number for Timeout.")
                return
            try:
                max_upload_mb = max(1, int(self.query_one("#max_upload_mb", Input).value.strip() or str(DEFAULTS["max_upload_size_mb"])))
            except ValueError:
                self.update_status("Enter a whole number for Max upload.")
                return

            if not api_key:
                self.update_status("Enter a Gemini API key or save one permanently before running.")
                return
            if not model:
                self.update_status("Choose a Gemini model before running.")
                return

            readiness = check_project_readiness(project_dir, api_key=api_key, model=str(model))
            if not readiness.ready:
                self.update_status(readiness.next_action)
                return

            schema_path = project_dir / "extraction_schema.yml"
            if (project_dir / "sources").exists() and not schema_path.exists():
                self.update_status("Confirm the default extraction schema before running.")
                self.push_screen(
                    DefaultSchemaModal(schema_path, project_dir),
                    lambda confirmed: self._handle_default_schema_choice(
                        confirmed,
                        project_dir=project_dir,
                        api_key=api_key,
                        model=str(model),
                        retry=retry,
                        parallel_requests=parallel_requests,
                        timeout_sec=timeout_sec,
                        max_upload_mb=max_upload_mb,
                    ),
                )
                return

            self._begin_run(
                project_dir=project_dir,
                api_key=api_key,
                model=str(model),
                retry=retry,
                parallel_requests=parallel_requests,
                timeout_sec=timeout_sec,
                max_upload_mb=max_upload_mb,
            )

        def _handle_default_schema_choice(
            self,
            confirmed: bool | None,
            *,
            project_dir: Path,
            api_key: str,
            model: str,
            retry: bool,
            parallel_requests: int,
            timeout_sec: int,
            max_upload_mb: int,
        ) -> None:
            schema_path = project_dir / "extraction_schema.yml"
            if not confirmed:
                self.update_status(
                    f"Run cancelled. Create or edit {display_path(schema_path, base=project_dir)} before running."
                )
                return

            try:
                project_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(DEFAULT_SCHEMA, schema_path)
            except Exception as exc:
                self.feedback(f"Could not create default schema: {exc}", severity="error")
                return

            self.feedback(
                f"Copied default schema to {display_path(schema_path, base=project_dir)}.",
                severity="information",
            )
            self._begin_run(
                project_dir=project_dir,
                api_key=api_key,
                model=model,
                retry=retry,
                parallel_requests=parallel_requests,
                timeout_sec=timeout_sec,
                max_upload_mb=max_upload_mb,
            )

        def _begin_run(
            self,
            *,
            project_dir: Path,
            api_key: str,
            model: str,
            retry: bool,
            parallel_requests: int,
            timeout_sec: int,
            max_upload_mb: int,
        ) -> None:
            self.set_busy(True)
            save_last_project_dir(project_dir)
            self.progress_widget.update(total=1, progress=0)
            self.progress_status_widget.update("Gemini processing: starting")
            self.update_status("Running..." if not retry else "Retrying failed files...")
            count, message = article_summary(project_dir)
            label = "↺ Retry" if retry else "▶ Run"
            self.log_widget.write("")
            self.log_widget.write(f"[bold]{label}[/bold]  [dim]{escape(display_path(project_dir))}[/dim]")
            self.log_widget.write(f"[dim]{escape(message)}[/dim]")
            if count:
                self.progress_widget.update(total=count, progress=0)
            worker = Thread(
                target=self._run_worker,
                kwargs={
                    "project_dir": project_dir,
                    "api_key": api_key,
                    "model": str(model),
                    "retry": retry,
                    "parallel_requests": parallel_requests,
                    "timeout_sec": timeout_sec,
                    "max_upload_mb": max_upload_mb,
                },
                daemon=True,
            )
            worker.start()

        def _run_worker(
            self,
            project_dir: Path,
            api_key: str,
            model: str,
            retry: bool,
            parallel_requests: int,
            timeout_sec: int,
            max_upload_mb: int,
        ) -> None:
            console = TextualConsole(self)
            started = time.monotonic()
            try:
                run_preflight(project_dir, interactive=False, console=console)
                existing = load_run_settings(project_dir)
                settings = RunSettings(
                    primary_model=normalize_model_name(model),
                    request_timeout_sec=timeout_sec,
                    parallel_requests=parallel_requests,
                    max_upload_size_mb=max_upload_mb,
                    effect_definition=existing.effect_definition if existing else None,
                )

                if retry:
                    summary = retry_project(
                        project_dir=project_dir,
                        settings=settings,
                        selected_sources=None,
                        assume_yes=True,
                        skip_report=False,
                        console=console,
                        api_key=api_key,
                    )
                else:
                    summary = run_project(
                        project_dir=project_dir,
                        settings=settings,
                        assume_yes=True,
                        interactive=False,
                        force=False,
                        skip_report=False,
                        console=console,
                        api_key=api_key,
                    )

                if summary is None:
                    self.call_from_thread(self.set_busy, False)
                    self.call_from_thread(self.update_status, "Add PDFs to sources/ before running.")
                    return

                elapsed = time.monotonic() - started
                mins, secs = divmod(int(elapsed), 60)
                runtime_str = f"{mins}m {secs}s" if mins else f"{secs}s"

                console.table(
                    "Summary",
                    ["Metric", "Value"],
                    [
                        (
                            "Report",
                            display_path(summary.report_path, base=project_dir)
                            if summary.report_path
                            else "not rendered",
                        ),
                        ("Successful PDFs", summary.successful_pdfs),
                        ("Failed PDFs", summary.failed_pdfs),
                        ("Extracted effects", summary.extracted_effects),
                        ("Usable z-curve inputs", summary.usable_zcurve_inputs),
                        ("Input tokens", f"{summary.input_tokens:,}"),
                        ("Output tokens", f"{summary.output_tokens:,}"),
                        ("Total tokens", f"{summary.total_tokens:,}"),
                        ("Runtime", runtime_str),
                    ],
                )

                zcurve_summary = read_zcurve_summary(project_dir)
                if zcurve_summary:
                    console._section("Z-Curve Results")
                    console.highlight(zcurve_summary)

                rows = failed_pdf_rows(project_dir)
                if rows:
                    console.table(
                        "Failed Articles",
                        ["Source", "Error"],
                        rows,
                    )

                console._write("")
                console.success(f"Done in {runtime_str}.")

                self.last_report_path = summary.report_path
                self.call_from_thread(self.set_busy, False)
                self.call_from_thread(
                    self.update_status,
                    "Finished. Open the report or retry failed PDFs." if summary.failed_pdfs else "Finished. Open the report when ready.",
                )
            except Exception as exc:
                summary = classify_error(exc)
                console.error(summary.technical_detail or str(exc))
                self.call_from_thread(self.set_busy, False)
                self.call_from_thread(self.update_status, summary.compact())

    AutoZCurveApp().run()
    return 0
