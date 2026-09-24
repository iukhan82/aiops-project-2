"""Opaque pagination cursors, shared by every list endpoint: base64 of the sort key of the last row served, never an offset."""

from __future__ import annotations

import base64
import json

from fastapi import HTTPException


def encode_cursor(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, sort_keys=True).encode()).decode()


def decode_cursor(cursor: str | None) -> dict | None:
    if cursor is None:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"invalid cursor: {exc}") from exc
