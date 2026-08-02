from __future__ import annotations

from collections.abc import Iterable


def redact_secrets(value: object, secrets: Iterable[str | None]) -> str:
    text = str(value)
    for secret in secrets:
        secret = (secret or "").strip()
        if secret:
            text = text.replace(secret, "[redacted]")
    return text
