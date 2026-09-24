"""P09.05 (CTL-16): recursive redaction of secrets, credentials and locations.

One function decides what may be written where a person or a file will read it later: audit detail, the policy decision record,
messages stored on a command, log lines, and the evidence files the verification scripts write into the repository. It is applied
BEFORE the value is stored, and the database refuses a JWT or private key that gets past it anyway (migration 0026), so a mistake in a
call site cannot quietly persist a secret.

Two things are redacted, recursively through dicts, lists and text:

* **secrets** - by key name (`password`, `token`, `authorization`, `cookie`, `secret`, `api_key`, ...) and by shape (a JWT, a
  `Bearer`/`Basic` credential, a PEM private key, `password=...` in text, credentials or an `access_token=` in a URL);
* **locations** - by key name (`lat`, `lon`, `latitude`, `longitude`, `location`, `coordinates`, `position`, `geom`, `geometry`),
  replaced entirely in the `export` mode that leaves the platform, kept in the `audit` mode where the trail's own reader (the auditor)
  needs to see what was acted on. Email addresses are masked in both.

Usernames and roles are NOT redacted: attribution is the point of an audit trail. Hashes are not redacted either: the chain is
useless if its proof is hidden.
"""

from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "[redacted]"
LOCATION = "[location]"
MAX_DEPTH = 12
MAX_TEXT = 4000

SECRET_KEY = re.compile(
    r"(password|passwd|passphrase|secret|token|authorization|cookie|api[_-]?key|private[_-]?key|credential|bearer)",
    re.IGNORECASE,
)
LOCATION_KEY = re.compile(
    r"^(lat|lon|lng|latitude|longitude|location|coordinates?|position|geom|geometry|gps)$",
    re.IGNORECASE,
)
# Keys that mention a secret word but hold no secret: counts, flags and names of things.
SECRET_KEY_ALLOWED = re.compile(
    r"(_count|_expired|_required|_ttl|_lifetime|_type|_id_hash|token_endpoint|tokens_seen|has_)",
    re.IGNORECASE,
)

TEXT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
        ),
        REDACTED,
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), REDACTED),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + REDACTED),
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|access_token|refresh_token|id_token|client_secret)(\s*[=:]\s*)(?!\[redacted\])[^\s,;&\"']+"
        ),
        r"\1\2" + REDACTED,
    ),
    (
        re.compile(
            r"(?i)([?&](?:access_token|refresh_token|id_token|token|code|password|secret|client_secret)=)[^&\s\"']+"
        ),
        r"\1" + REDACTED,
    ),
    (re.compile(r"(://)[^/\s:@]+:[^/\s@]+@"), r"\1" + REDACTED + "@"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"), "[email]"),
)


def redact_text(text: str) -> str:
    """Secrets and email addresses in free text. Long text is cut: nothing this platform stores in an audit detail needs 4 kB of prose."""
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + " [truncated]"
    for pattern, replacement in TEXT_RULES:
        text = pattern.sub(replacement, text)
    return text


def redact(value: Any, mode: str = "audit", _depth: int = 0) -> Any:
    """A copy of `value` with secrets removed. `mode` is "audit" (locations kept) or "export" (locations removed too)."""
    if _depth > MAX_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if (
                SECRET_KEY.search(name)
                and not SECRET_KEY_ALLOWED.search(name)
                and item not in (None, "", False, True)
            ):
                out[name] = REDACTED
            elif mode == "export" and LOCATION_KEY.match(name) and item is not None:
                out[name] = LOCATION
            else:
                out[name] = redact(item, mode, _depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [redact(item, mode, _depth + 1) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


class RedactingFilter(logging.Filter):
    """Attach to a logger or handler: the formatted message is redacted before any handler writes it. uvicorn's access log records the
    request line, which for a WebSocket carries the access token in the query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a malformed log call must not break logging
            return True
        clean = redact_text(message)
        if clean != message:
            record.msg, record.args = clean, ()
        return True


def install_log_redaction() -> None:
    """Redact every record the process logs, including uvicorn's own loggers (handlers are filtered, so propagation does not matter)."""
    filter_ = RedactingFilter()
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(filter_)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "aiops"):
        logger = logging.getLogger(name)
        logger.addFilter(filter_)
        for handler in logger.handlers:
            handler.addFilter(filter_)
