from __future__ import annotations

import io
import sys
from contextlib import contextmanager
from typing import Iterable, Iterator

from .security import redact_secrets


try:
    from rich.console import Console
    from rich.errors import MarkupError
    from rich.markup import escape
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table
    from rich.text import Text
except Exception:  # pragma: no cover - exercised when optional deps are absent
    Console = None
    Panel = None
    Progress = None
    Table = None
    Text = None


class CliConsole:
    def __init__(self, secrets: list[str] | None = None) -> None:
        self.rich = Console(highlight=False) if Console else None
        # API keys to redact from everything this console prints.
        self.secrets = list(secrets or [])

    def redact(self, value: object) -> str:
        if not self.rich:
            return redact_secrets(value, self.secrets)
        text = str(value)
        # Rich reads markup after this, so a tag inside a key (`ab[red]cd[/red]`)
        # would hide it from a search of the raw text. Check the text Rich will
        # show; when it holds a key, print that text literally, redacted.
        try:
            shown = Text.from_markup(text).plain
        except MarkupError:
            # Not valid markup, so Rich would fail on it: print it literally.
            return escape(redact_secrets(text, self.secrets))
        if redact_secrets(shown, self.secrets) == shown and redact_secrets(text, self.secrets) == text:
            return text
        return escape(redact_secrets(shown, self.secrets))

    def _redact_part(self, part: object) -> object:
        """Redact one printed part, whatever its type."""
        if isinstance(part, str) or not self.rich:
            return self.redact(part)
        if not self.secrets:
            return part
        # A Rich renderable's text exists only once rendered, so render it
        # without styles (a style change could split a key), redact it, and
        # print the result literally.
        plain = Console(file=io.StringIO(), width=self.rich.width, color_system=None, highlight=False)
        plain.print(part, end="")
        return Text(redact_secrets(plain.file.getvalue(), self.secrets))

    def print(self, *parts: object) -> None:
        parts = tuple(self._redact_part(part) for part in parts)
        if self.rich:
            self.rich.print(*parts)
        else:
            print(*parts)

    def title(self, text: str, subtitle: str | None = None) -> None:
        text = self.redact(text)
        subtitle = None if subtitle is None else self.redact(subtitle)
        if self.rich and Panel:
            body = text if subtitle is None else f"{text}\n{subtitle}"
            self.rich.print(Panel.fit(body, title="auto-zcurve", border_style="cyan"))
        else:
            print(f"auto-zcurve: {text}")
            if subtitle:
                print(subtitle)

    def info(self, text: str) -> None:
        self.print(f"[cyan]{text}[/cyan]" if self.rich else text)

    def warn(self, text: str) -> None:
        self.print(f"[yellow]{text}[/yellow]" if self.rich else f"Warning: {text}")

    def error(self, text: str) -> None:
        self.print(f"[red]{text}[/red]" if self.rich else f"Error: {text}")

    def success(self, text: str) -> None:
        self.print(f"[green]{text}[/green]" if self.rich else text)

    def table(self, title: str, columns: Iterable[str], rows: Iterable[Iterable[object]]) -> None:
        title = self.redact(title)
        columns = [self.redact(column) for column in columns]
        rows = [[self.redact(item) for item in row] for row in rows]
        if self.rich and Table:
            table = Table(title=title)
            for column in columns:
                table.add_column(column)
            for row in rows:
                table.add_row(*(str(item) for item in row))
            self.rich.print(table)
            return

        print(title)
        print(" | ".join(columns))
        for row in rows:
            print(" | ".join(str(item) for item in row))

    @contextmanager
    def progress(self, total: int, description: str) -> Iterator[object | None]:
        description = self.redact(description)
        if self.rich and Progress:
            progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=self.rich,
            )
            with progress:
                task_id = progress.add_task(description, total=total)
                yield _RichProgressAdapter(progress, task_id, self.redact)
            return

        print(description)
        yield _PlainProgressAdapter(total, self.redact)


class _RichProgressAdapter:
    def __init__(self, progress: object, task_id: object, redact=str) -> None:
        self.progress = progress
        self.task_id = task_id
        self.redact = redact

    def advance(self, description: str | None = None) -> None:
        kwargs = {"advance": 1}
        if description:
            kwargs["description"] = self.redact(description)
        self.progress.update(self.task_id, **kwargs)


class _PlainProgressAdapter:
    def __init__(self, total: int, redact=str) -> None:
        self.total = total
        self.done = 0
        self.redact = redact

    def advance(self, description: str | None = None) -> None:
        self.done += 1
        message = self.redact(description) if description else "Processed"
        print(f"{message}: {self.done}/{self.total}", file=sys.stderr)
