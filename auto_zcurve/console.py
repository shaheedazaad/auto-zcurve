from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterable, Iterator


try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table
except Exception:  # pragma: no cover - exercised when optional deps are absent
    Console = None
    Panel = None
    Progress = None
    Table = None


class CliConsole:
    def __init__(self) -> None:
        self.rich = Console(highlight=False) if Console else None

    def print(self, *parts: object) -> None:
        if self.rich:
            self.rich.print(*parts)
        else:
            print(*parts)

    def title(self, text: str, subtitle: str | None = None) -> None:
        if self.rich and Panel:
            body = text if subtitle is None else f"{text}\n{subtitle}"
            self.rich.print(Panel.fit(body, title="Auto Z-Curve", border_style="cyan"))
        else:
            print(f"Auto Z-Curve: {text}")
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
                yield _RichProgressAdapter(progress, task_id)
            return

        print(description)
        yield _PlainProgressAdapter(total)


class _RichProgressAdapter:
    def __init__(self, progress: object, task_id: object) -> None:
        self.progress = progress
        self.task_id = task_id

    def advance(self, description: str | None = None) -> None:
        kwargs = {"advance": 1}
        if description:
            kwargs["description"] = description
        self.progress.update(self.task_id, **kwargs)


class _PlainProgressAdapter:
    def __init__(self, total: int) -> None:
        self.total = total
        self.done = 0

    def advance(self, description: str | None = None) -> None:
        self.done += 1
        message = description or "Processed"
        print(f"{message}: {self.done}/{self.total}", file=sys.stderr)
