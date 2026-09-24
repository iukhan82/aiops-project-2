"""P04.07: durable offline outbox - order, acknowledgement, idempotent
enqueue, bounded quota, and crash/restart recovery with no accepted
duplicates end to end (outbox -> EdgeValidator's own duplicate check)."""

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from edge_helpers import GEOMETRY, T0, event, make_validator

from edge.outbox import DurableOutbox, OutboxFull, OutboxSink, drain
from edge.validation import Status


def _ev(n: int, kind: str = "x") -> dict:
    return {"event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{kind}-{n}")), "n": n, "kind": kind}


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "corridor-a.sqlite3"


# --- basic append / pending / ack --------------------------------------------------


def test_append_then_pending_preserves_enqueue_order(db_path: Path) -> None:
    with DurableOutbox(db_path) as box:
        for i in range(5):
            box.append(_ev(i))
        ids = [e.event_id for e in box.pending()]
        assert ids == [_ev(i)["event_id"] for i in range(5)]
        assert all(not e.acked for e in box.pending())


def test_duplicate_append_is_a_noop_not_a_second_row(db_path: Path) -> None:
    with DurableOutbox(db_path) as box:
        assert box.append(_ev(1)) is True
        assert box.append(_ev(1)) is False  # same event_id, idempotent
        assert len(box.pending()) == 1
        assert box.counts() == {"pending": 1, "acked": 0}


def test_ack_removes_from_pending_but_keeps_history_row(db_path: Path) -> None:
    with DurableOutbox(db_path) as box:
        box.append(_ev(1))
        box.append(_ev(2))
        assert box.ack(_ev(1)["event_id"]) is True
        assert [e.event_id for e in box.pending()] == [_ev(2)["event_id"]]
        history = box.all_entries()
        assert [e.event_id for e in history] == [_ev(1)["event_id"], _ev(2)["event_id"]]
        acked_entry = next(e for e in history if e.event_id == _ev(1)["event_id"])
        assert acked_entry.acked and acked_entry.payload is None  # payload cleared on ack


def test_acking_an_unknown_or_already_acked_id_is_a_noop(db_path: Path) -> None:
    with DurableOutbox(db_path) as box:
        box.append(_ev(1))
        assert box.ack("not-a-real-id") is False
        assert box.ack(_ev(1)["event_id"]) is True
        assert box.ack(_ev(1)["event_id"]) is False  # already acked


def test_ack_many_and_prune_acked(db_path: Path) -> None:
    with DurableOutbox(db_path) as box:
        for i in range(4):
            box.append(_ev(i))
        acked = box.ack_many([_ev(0)["event_id"], _ev(2)["event_id"]])
        assert acked == 2
        assert box.counts() == {"pending": 2, "acked": 2}
        removed = box.prune_acked()
        assert removed == 2
        assert len(box.all_entries()) == 2
        assert all(not e.acked for e in box.all_entries())


def test_pending_limit_still_respects_order(db_path: Path) -> None:
    with DurableOutbox(db_path) as box:
        for i in range(10):
            box.append(_ev(i))
        first_three = box.pending(limit=3)
        assert [e.event_id for e in first_three] == [_ev(i)["event_id"] for i in range(3)]


# --- quota -------------------------------------------------------------------------


def test_quota_warning_and_hard_limit_thresholds(db_path: Path) -> None:
    payload_bytes = len(json.dumps(_ev(0), sort_keys=True).encode("utf-8"))
    quota = payload_bytes * 10
    with DurableOutbox(db_path, quota_bytes=quota, warn_fraction=0.5, hard_fraction=0.8) as box:
        for i in range(4):
            box.append(_ev(i))
        status = box.quota_status()
        assert not status.warning and not status.full
        for i in range(4, 7):
            box.append(_ev(i))
        status = box.quota_status()
        assert status.warning and not status.full


def test_append_refused_at_hard_quota_not_silently_dropped(db_path: Path) -> None:
    payload_bytes = len(json.dumps(_ev(0), sort_keys=True).encode("utf-8"))
    quota = payload_bytes * 4
    with DurableOutbox(db_path, quota_bytes=quota, hard_fraction=0.9) as box:
        for i in range(4):
            box.append(_ev(i))
        with pytest.raises(OutboxFull):
            box.append(_ev(99))
        assert len(box.pending()) == 4  # the refused event never entered the outbox


def test_acking_frees_quota_for_more_appends(db_path: Path) -> None:
    payload_bytes = len(json.dumps(_ev(0), sort_keys=True).encode("utf-8"))
    quota = payload_bytes * 4
    with DurableOutbox(db_path, quota_bytes=quota, hard_fraction=0.9) as box:
        for i in range(4):
            box.append(_ev(i))
        with pytest.raises(OutboxFull):
            box.append(_ev(99))
        box.ack(_ev(0)["event_id"])
        assert box.append(_ev(99)) is True  # quota counts unacked backlog only


# --- crash / restart recovery -------------------------------------------------------


def test_committed_rows_survive_without_ever_calling_close(db_path: Path) -> None:
    """Simulates `kill -9`: no close(), no flush - just stop touching the
    object and open a fresh connection against the same file."""
    box = DurableOutbox(db_path)
    for i in range(5):
        box.append(_ev(i))
    box.ack(_ev(1)["event_id"])
    del box  # no close(): the process is gone, not shut down

    reopened = DurableOutbox(db_path)
    try:
        pending_ids = [e.event_id for e in reopened.pending()]
        assert pending_ids == [_ev(i)["event_id"] for i in (0, 2, 3, 4)]
        assert reopened.counts() == {"pending": 4, "acked": 1}
    finally:
        reopened.close()


def test_two_open_connections_see_the_same_committed_state(db_path: Path) -> None:
    """WAL mode: a second reader sees rows the first connection committed,
    without the first ever closing - proves durability is per-write, not
    per-connection-close."""
    writer = DurableOutbox(db_path)
    writer.append(_ev(1))
    reader = DurableOutbox(db_path)
    try:
        assert [e.event_id for e in reader.pending()] == [_ev(1)["event_id"]]
        writer.append(_ev(2))
        assert [e.event_id for e in reader.pending()] == [_ev(1)["event_id"], _ev(2)["event_id"]]
    finally:
        reader.close()
        writer.close()


def test_corrupted_file_raises_rather_than_silently_starting_empty(tmp_path: Path) -> None:
    bad = tmp_path / "not-a-db.sqlite3"
    bad.write_bytes(b"this is not a sqlite file at all, just garbage bytes")
    with pytest.raises(sqlite3.DatabaseError):
        DurableOutbox(bad)


# --- ordered, acknowledged, idempotent drain (the "uplink") ------------------------


def test_drain_sends_in_order_and_only_acks_on_success(db_path: Path) -> None:
    with DurableOutbox(db_path) as box:
        for i in range(5):
            box.append(_ev(i))
        sent_order = []

        def flaky_send(payload: dict) -> bool:
            sent_order.append(payload["n"])
            return payload["n"] != 3  # fails on the 4th event

        result = drain(box, flaky_send)
        assert sent_order == [0, 1, 2, 3]  # stopped at the failure, did not skip ahead
        assert result == {"sent": 4, "acked": 3}
        assert [e.event_id for e in box.pending()] == [_ev(i)["event_id"] for i in (3, 4)]


def test_resumed_drain_after_uplink_recovers_continues_in_order(db_path: Path) -> None:
    with DurableOutbox(db_path) as box:
        for i in range(5):
            box.append(_ev(i))
        drain(box, lambda p: p["n"] != 2)  # jams at event 2
        sent_order = []
        result = drain(box, lambda p: (sent_order.append(p["n"]), True)[1])  # uplink recovers
        assert sent_order == [2, 3, 4]
        assert result == {"sent": 3, "acked": 3}
        assert box.pending() == []


def test_uplink_outage_then_recovery_preserves_all_events_and_order(db_path: Path) -> None:
    """Buffer while fully offline, then drain everything once the uplink returns."""
    with DurableOutbox(db_path) as box:
        for i in range(50):
            box.append(_ev(i))
        assert box.counts() == {"pending": 50, "acked": 0}
        sent_order = []
        result = drain(box, lambda p: (sent_order.append(p["n"]), True)[1])
        assert sent_order == list(range(50))
        assert result == {"sent": 50, "acked": 50}
        assert box.counts() == {"pending": 0, "acked": 50}


# --- end-to-end: crash mid-flight still yields "no accepted duplicates" -----------


def test_crash_before_ack_resends_but_receiver_rejects_the_duplicate(db_path: Path) -> None:
    """The realistic failure: the event was sent and the receiver accepted
    it, but the process crashed before the ack was recorded locally. After
    restart the (unacked) event is resent with the *same* event_id, and the
    receiver's own duplicate check - the same one every other event source
    in this project goes through - rejects the resend. No event is ever
    processed twice downstream."""
    validator = make_validator()
    now = T0
    e1 = event("loop-a", seq=0, obs=T0)
    e2 = event("loop-a", seq=1, obs=T0)

    box = DurableOutbox(db_path)
    box.append(e1)
    box.append(e2)

    # "sent" e1 to the receiver and it was accepted, but the ack never made
    # it back before the crash - box.ack() is never called for e1.
    first_delivery = validator.validate(e1, now)
    assert first_delivery.status is not Status.REJECTED
    del box  # crash: no close(), no ack persisted

    reopened = DurableOutbox(db_path)
    try:
        resent_ids = [e.event_id for e in reopened.pending()]
        assert resent_ids == [e1["event_id"], e2["event_id"]]  # e1 resent: never acked

        outcomes = [validator.validate(reopened.pending()[i].payload, now) for i in range(2)]
        assert outcomes[0].status is Status.REJECTED
        assert outcomes[0].reasons == ("duplicate_event_id",)  # the resend is rejected
        assert outcomes[1].status is not Status.REJECTED  # e2, genuinely new, is accepted

        for entry in reopened.pending():
            reopened.ack(entry.event_id)
        assert reopened.counts() == {"pending": 0, "acked": 2}
    finally:
        reopened.close()


def test_outbox_sink_integrates_with_the_runtime_event_sink_protocol(db_path: Path) -> None:
    with DurableOutbox(db_path) as box:
        sink = OutboxSink(box)
        sink.emit({"event_id": str(uuid.uuid4()), "hello": "world"})
        sink.emit({"event_id": str(uuid.uuid4()), "hello": "again"})
        assert box.counts() == {"pending": 2, "acked": 0}


def test_geometry_constant_reference_for_helper_reuse() -> None:
    assert GEOMETRY == "2026-09-18.1"  # sanity: shared fixture module imported correctly
