"""P05.07: API pure-logic unit tests (cursor encode/decode). Real-database
and real-server behavior (pagination against live data, live-feed
reconnect, device scoping) needs the real P05.01/P05.04 stack running
under uvicorn and is proven by source-code/backend/api/verify_api.py, not
here - see docs/evidence/p05_07_api.json.
"""

import pytest
from fastapi import HTTPException

from backend.api.app import decode_cursor, encode_cursor


def test_cursor_round_trips() -> None:
    original = {"device_id": "corridor-a-int-03-loop-01"}
    assert decode_cursor(encode_cursor(original)) == original


def test_decode_none_cursor_is_none() -> None:
    assert decode_cursor(None) is None


def test_decode_garbage_cursor_raises_400() -> None:
    with pytest.raises(HTTPException) as exc_info:
        decode_cursor("not-a-valid-cursor!!!")
    assert exc_info.value.status_code == 400
