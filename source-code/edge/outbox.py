"""P04.07: durable offline outbox (ADR-0006: SQLite, WAL mode, one file per
logical edge identity) with an acknowledgement-tracked replay cursor.

docs/PROJECT_CONTEXT.md: "Offline edges continue safe local processing and
buffer accepted events. Replay is ordered, acknowledged, bounded, idempotent,
and duplicate-safe." Concretely:

- **Durable**: WAL mode + a committed transaction per `append`, so a killed
  process (not just a clean `close()`) leaves every committed row on disk;
  proven by opening a *second, independent* connection against the same file
  without ever closing the first (`test_outbox_crash_recovery.py`-style
  tests simulate `kill -9`, not a graceful shutdown).
- **Ordered**: `pending()` yields rows by an autoincrementing primary key -
  the original enqueue order - regardless of which rows were later acked.
- **Acknowledged**: a row is only ever removed by `ack()`, which the caller
  invokes solely on a confirmed uplink/application acknowledgement. An
  event that was sent but crashed before its ack arrived stays pending and
  is resent - this outbox does not by itself prevent a downstream duplicate
  delivery (no local buffer can, across a crash mid-flight); it guarantees
  the resend carries the *same* `event_id`, which is what lets the receiver
  (`edge.validation.EdgeValidator`'s duplicate_event_id check, or
  `simulator/manifest/replay.py`'s replay) reject it as a duplicate rather
  than accept it twice - "no accepted duplicates" is a property of the
  (outbox, receiver) pair, proven end to end in the tests.
- **Idempotent enqueue**: `append` upserts by `event_id`, so enqueueing the
  same event twice (e.g. a retried validation call) never creates two rows.
- **Bounded**: a byte quota (RESOURCE_BUDGET.md: 256 MiB per edge, warn at
  70%, hard limit before 90%) is enforced on the *unacked* backlog; past the
  hard limit `append` raises `OutboxFull` rather than silently dropping
  data or growing unbounded.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_QUOTA_BYTES = 256 * 1024 * 1024
DEFAULT_WARN_FRACTION = 0.70
DEFAULT_HARD_FRACTION = 0.90

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    payload_bytes INTEGER NOT NULL,
    enqueued_at REAL NOT NULL,
    acked INTEGER NOT NULL DEFAULT 0,
    acked_at REAL
);
CREATE INDEX IF NOT EXISTS outbox_acked_idx ON outbox (acked, seq);
"""


class OutboxError(Exception):
    pass


class OutboxFull(OutboxError):
    """Unacked backlog is at or above the hard quota; append refused."""


@dataclass(frozen=True)
class OutboxEntry:
    seq: int
    event_id: str
    payload: dict | None  # None once acked: the payload is cleared on ack
    enqueued_at: float
    acked: bool
    acked_at: float | None


@dataclass(frozen=True)
class QuotaStatus:
    used_bytes: int
    quota_bytes: int
    warn_bytes: int
    hard_bytes: int

    @property
    def fraction(self) -> float:
        return self.used_bytes / self.quota_bytes if self.quota_bytes else 1.0

    @property
    def warning(self) -> bool:
        return self.used_bytes >= self.warn_bytes

    @property
    def full(self) -> bool:
        return self.used_bytes >= self.hard_bytes


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")  # crash-safety over raw throughput
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


class DurableOutbox:
    """One instance per logical edge identity, one SQLite file per instance
    (ADR-0006). Safe for a single writer/reader (the edge process); not a
    multi-writer queue."""

    def __init__(
        self,
        path: Path,
        quota_bytes: int = DEFAULT_QUOTA_BYTES,
        warn_fraction: float = DEFAULT_WARN_FRACTION,
        hard_fraction: float = DEFAULT_HARD_FRACTION,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.quota_bytes = quota_bytes
        self.warn_bytes = int(quota_bytes * warn_fraction)
        self.hard_bytes = int(quota_bytes * hard_fraction)
        self._conn = _connect(self.path)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DurableOutbox:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- write path -----------------------------------------------------------------

    def append(self, event: dict, now: float | None = None) -> bool:
        """Enqueue one event. Returns False (no-op) if this event_id is
        already present (idempotent enqueue), True if a new row was added.
        Raises OutboxFull if the unacked backlog is at the hard quota."""
        event_id = event["event_id"]
        existing = self._conn.execute(
            "SELECT 1 FROM outbox WHERE event_id = ?", (event_id,)
        ).fetchone()
        if existing:
            return False
        status = self.quota_status()
        if status.full:
            raise OutboxFull(
                f"unacked backlog {status.used_bytes}B >= hard limit {status.hard_bytes}B"
            )
        payload = json.dumps(event, sort_keys=True)
        self._conn.execute(
            "INSERT INTO outbox (event_id, payload, payload_bytes, enqueued_at) VALUES (?, ?, ?, ?)",
            (
                event_id,
                payload,
                len(payload.encode("utf-8")),
                now if now is not None else time.time(),
            ),
        )
        return True

    # -- read / replay path -----------------------------------------------------------

    def pending(self, limit: int | None = None) -> list[OutboxEntry]:
        """Unacked rows in original enqueue order (FIFO); the replay cursor
        is simply 'the first unacked row', so a crash mid-replay always
        resumes at the correct point without separate cursor bookkeeping."""
        sql = "SELECT seq, event_id, payload, enqueued_at, acked, acked_at FROM outbox WHERE acked = 0 ORDER BY seq ASC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [self._row(r) for r in self._conn.execute(sql).fetchall()]

    def all_entries(self) -> list[OutboxEntry]:
        sql = "SELECT seq, event_id, payload, enqueued_at, acked, acked_at FROM outbox ORDER BY seq ASC"
        return [self._row(r) for r in self._conn.execute(sql).fetchall()]

    @staticmethod
    def _row(r: tuple) -> OutboxEntry:
        seq, event_id, payload, enqueued_at, acked, acked_at = r
        parsed = json.loads(payload) if payload else None
        return OutboxEntry(seq, event_id, parsed, enqueued_at, bool(acked), acked_at)

    def ack(self, event_id: str, now: float | None = None) -> bool:
        """Mark one event acknowledged and evict its payload; only call this
        on a confirmed uplink acknowledgement, never speculatively."""
        cur = self._conn.execute(
            "UPDATE outbox SET acked = 1, acked_at = ?, payload = '' WHERE event_id = ? AND acked = 0",
            (now if now is not None else time.time(), event_id),
        )
        return cur.rowcount > 0

    def ack_many(self, event_ids: list[str], now: float | None = None) -> int:
        return sum(self.ack(eid, now) for eid in event_ids)

    def prune_acked(self) -> int:
        """Physically remove acked rows (payload is already cleared on ack;
        this reclaims the row/index space). Safe at any time."""
        cur = self._conn.execute("DELETE FROM outbox WHERE acked = 1")
        return cur.rowcount

    # -- bookkeeping --------------------------------------------------------------

    def quota_status(self) -> QuotaStatus:
        (used,) = self._conn.execute(
            "SELECT COALESCE(SUM(payload_bytes), 0) FROM outbox WHERE acked = 0"
        ).fetchone()
        return QuotaStatus(used, self.quota_bytes, self.warn_bytes, self.hard_bytes)

    def counts(self) -> dict[str, int]:
        pending, acked = self._conn.execute(
            "SELECT SUM(acked = 0), SUM(acked = 1) FROM outbox"
        ).fetchone()
        return {"pending": pending or 0, "acked": acked or 0}


class OutboxSink:
    """edge.runtime.EventSink adapter: derived events are durably enqueued,
    not handed directly to a transport - crash/restart/uplink loss between
    "the runtime decided to emit this" and "a broker actually has it" never
    loses the event, per docs/PROJECT_CONTEXT.md's offline-buffering rule."""

    def __init__(self, outbox: DurableOutbox) -> None:
        self.outbox = outbox

    def emit(self, event: dict) -> None:
        self.outbox.append(event)


def drain(
    outbox: DurableOutbox, send: Callable[[dict], bool], limit: int | None = None
) -> dict[str, int]:
    """Replay pending entries in order to `send`; ack only on success, and
    stop at the first failure so a transient uplink loss never causes a
    later event to be acked/removed ahead of an earlier one still pending -
    order is preserved across resumed drains, not just within one call."""
    sent = acked = 0
    for entry in outbox.pending(limit):
        sent += 1
        if not send(entry.payload):
            break
        outbox.ack(entry.event_id)
        acked += 1
    return {"sent": sent, "acked": acked}
