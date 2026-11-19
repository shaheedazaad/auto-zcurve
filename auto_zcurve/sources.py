"""Sources: the article files in a project, and what each source format needs.

A source is either a PDF or an XML file. PDFs go to the model as PDFs (or as
text the provider extracts from them). XML sources are never parsed: their
contents are sent to the model as article text, exactly as uploaded.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

SOURCE_FORMATS = {".pdf": "pdf", ".xml": "xml"}
SOURCE_ACCEPT = "application/pdf,.pdf,application/xml,text/xml,.xml"
XML_TEXT_INPUT_MODE = "xml_text"

# A rough characters-per-token ratio for article text. XML markup usually
# needs more tokens per character than prose, so this tends to underestimate,
# and an article it puts over a model's limit is clearly over.
CHARS_PER_TOKEN = 3


class SourceError(ValueError):
    """A source file that cannot be used as it is."""


def source_format(path: Path | str) -> str | None:
    """Return 'pdf' or 'xml' for a source filename (any case), else None."""
    return SOURCE_FORMATS.get(Path(path).suffix.lower())


def is_source_name(name: str) -> bool:
    return source_format(name) is not None


def discover_sources(project_dir: Path, *, recursive: bool = True) -> list[Path]:
    """List a project's PDF and XML sources, sorted by path.

    The CLI reads nested folders under sources/; the web app only manages the
    top level, so it passes recursive=False.
    """
    sources = project_dir / "sources"
    if not sources.is_dir():
        return []
    candidates = sources.rglob("*") if recursive else sources.iterdir()
    return sorted(path for path in candidates if path.is_file() and is_source_name(path.name))


def decode_xml(data: bytes, name: str) -> str:
    """Return an XML source's text, or raise SourceError saying how to fix it.

    The bytes are only decoded and sniffed, never parsed, so no DTD, entity
    or network lookup can happen.
    """
    if not data.strip():
        raise SourceError(f"{name} is empty. Upload the article's full XML file.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SourceError(
            f"{name} is not UTF-8 text. Save the XML as UTF-8 and add it again."
        ) from None
    if "\x00" in text:
        raise SourceError(f"{name} is not a text file. Add the article's XML file instead.")
    if not text.lstrip().startswith("<"):
        raise SourceError(
            f"{name} does not look like XML (it should start with '<'). Add the article's XML file instead."
        )
    return text


def read_article_text(path: Path) -> str:
    """Return an XML source's contents as article text."""
    return decode_xml(path.read_bytes(), path.name)


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _tokens_display(count: int) -> str:
    return f"{round(count / 1000)}k" if count >= 1000 else str(count)


def check_context_length(estimated_tokens: int, context_length: int | None) -> None:
    """Fail when article text is clearly too long for the model; never truncate it."""
    if not context_length or context_length <= 0 or estimated_tokens <= context_length:
        return
    raise SourceError(
        f"Article is ~{_tokens_display(estimated_tokens)} tokens; "
        f"this model accepts {_tokens_display(context_length)}."
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
