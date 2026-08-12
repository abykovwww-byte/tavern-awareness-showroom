"""Small shared redactor for diagnostic payloads that may contain credentials."""

from __future__ import annotations

import re
from typing import Any, Iterable


REDACTED = "[REDACTED]"
_SECRET_KEY_RE = re.compile(
    r"(?i)^(?:(?:x[-_])?api[-_]?key|authorization|cookie|password|(?:access[-_]|refresh[-_])?token|client[-_]?secret|secret)$"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)([\"']?(?:(?:x[-_])?api[-_]?key|authorization|cookie|password|"
    r"(?:access[-_]|refresh[-_])token|client[-_]?secret)[\"']?\s*[:=]\s*)"
    r"(?:\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\s,;}\]]+)"
)


def redact_trace_value(value: Any, known_secrets: Iterable[str | None] = ()) -> Any:
    """Recursively redact known values and common credential shapes."""

    secrets = tuple(sorted({item for item in known_secrets if item}, key=len, reverse=True))
    if isinstance(value, dict):
        return {
            key: REDACTED if _SECRET_KEY_RE.fullmatch(str(key)) else redact_trace_value(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_trace_value(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    redacted = value
    for secret in secrets:
        redacted = redacted.replace(secret, REDACTED)
    redacted = _BEARER_RE.sub(f"Bearer {REDACTED}", redacted)
    return _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
