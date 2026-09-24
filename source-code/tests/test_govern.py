"""P08.09: pure-logic tests for the governance endpoints. What needs the real stack (the trail over real tables, real probes, handover
races) is proven by backend/api/verify_govern.py - see docs/evidence/p08_09_evidence.json."""

from datetime import timezone

import httpx
import psycopg
import pytest
from fastapi import HTTPException

from backend.api.cursor import decode_cursor, encode_cursor
from backend.api.routes_govern import (
    AUDIT_COLUMNS,
    AUDIT_SOURCES,
    DEVICE_STALE_AFTER_S,
    WORKERS,
    _explain,
    _iso,
    _like,
    _moment,
)


def test_actor_filter_text_is_never_a_wildcard() -> None:
    assert _like("alex") == "%alex%"
    assert _like("100%") == r"%100\%%"
    assert _like("a_b") == r"%a\_b%"
    assert _like("\\") == r"%\\%"


def test_times_are_parsed_as_utc_and_a_bad_one_is_refused_with_a_reason() -> None:
    assert _moment("2026-09-21T08:00:00Z", "since").tzinfo == timezone.utc
    assert _moment("2026-09-21T08:00:00", "since").tzinfo == timezone.utc
    assert _moment(None, "since") is None
    with pytest.raises(HTTPException) as caught:
        _moment("yesterday-ish", "since")
    assert caught.value.status_code == 422 and caught.value.detail["error"] == "bad_time"


def test_iso_output_is_utc_with_milliseconds() -> None:
    from datetime import datetime

    assert (
        _iso(datetime(2026, 9, 21, 8, 0, 1, 234000, tzinfo=timezone.utc))
        == "2026-09-21T08:00:01.234Z"
    )
    assert _iso(None) is None


def test_probe_failures_are_worded_for_a_person_not_a_stack_trace() -> None:
    assert _explain(TimeoutError()) == "it did not answer in the time allowed"
    assert _explain(httpx.ConnectTimeout("x")) == "it did not answer in the time allowed"
    assert _explain(ConnectionRefusedError()) == "the connection was refused"
    assert _explain(psycopg.OperationalError("x")) == "the database could not be reached"
    assert "ValueError" in _explain(ValueError("secret detail"))
    assert "secret detail" not in _explain(ValueError("secret detail"))


def test_the_audit_union_gives_every_source_the_same_columns_and_workers_have_sane_budgets() -> (
    None
):
    assert AUDIT_SOURCES.count("UNION ALL") == 4
    assert AUDIT_COLUMNS[:3] == ("at", "source", "row_id")
    assert {name for name, _, _ in WORKERS} == {
        "demo-feeder",
        "command-executor",
        "outcome-verifier",
    }
    assert all(budget > 0 for _, _, budget in WORKERS)
    assert all(seconds > 0 for seconds in DEVICE_STALE_AFTER_S.values())


def test_cursors_round_trip_and_a_forged_one_is_a_400() -> None:
    value = {"at": "2026-09-21T08:00:00+00:00", "source": "operator", "row_id": 7}
    assert decode_cursor(encode_cursor(value)) == value
    assert decode_cursor(None) is None
    with pytest.raises(HTTPException) as caught:
        decode_cursor("not-a-cursor")
    assert caught.value.status_code == 400
