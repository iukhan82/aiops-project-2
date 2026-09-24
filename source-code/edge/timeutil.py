"""Strict RFC 3339 UTC timestamp parsing/formatting shared by edge modules."""

from __future__ import annotations

from datetime import datetime, timezone


def parse_ts(value: object) -> datetime:
    """Parse an ISO-8601/RFC 3339 timestamp with an explicit offset to UTC.

    Naive timestamps are rejected: the platform invariant is UTC everywhere.
    """
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must carry an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def format_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
