"""P10.07: platform incident repository. Writes what `backend.aiops.correlation` inferred and keeps the history honest.

`sync(conn, drafts, now)` makes the stored incidents match the drafts as they stand at `now`, in one transaction:

- a draft with an active signal and no live incident of its key opens one (or reopens one resolved within
  `REOPEN_WITHIN_S`, so a flapping alert is one incident, not a stream of them);
- a live incident gets its signals, severity, title and hypothesis brought up to date, and every change worth a reader's
  attention is one row in `platform_incident_events` (hash-chained and append-only in the database itself);
- a signal that moved to a different incident (a new root explains it) is cleared from the old one, and an incident left
  with nothing is resolved with `merged_into` recorded;
- an incident whose signals have all been quiet for `RESOLVE_AFTER_S` resolves.

A status a person set (`acknowledged`, `escalated`) is never overwritten by `sync`. The correlator's database role cannot
write `verified_cause` at all (column-level grant in migration 0029); `record_verified_cause` is for the application role.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.aiops.correlation import REOPEN_WITHIN_S, RESOLVE_AFTER_S, IncidentDraft, Signal

ACTOR = "svc_platform_correlator"

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "open": {"acknowledged", "escalated", "resolved"},
    "acknowledged": {"escalated", "resolved"},
    "escalated": {"acknowledged", "resolved"},
    "resolved": {"reopened"},
    "reopened": {"acknowledged", "escalated", "resolved"},
}


class IncidentError(Exception):
    pass


def _event(
    cur: psycopg.Cursor, incident_id: uuid.UUID, at: datetime, event: str, actor: str, detail: dict
) -> None:
    cur.execute(
        "INSERT INTO platform_incident_events (platform_incident_id, at, event, actor, detail) "
        "VALUES (%s, %s, %s, %s, %s)",
        (incident_id, at, event, actor, Jsonb(detail)),
    )


def _insert_signal(cur: psycopg.Cursor, incident_id: uuid.UUID, s: Signal) -> None:
    row = s.as_row()
    cur.execute(
        "INSERT INTO platform_incident_signals (platform_incident_id, signal_key, source, signal, component, labels, "
        "severity, first_seen, last_seen, occurrences, active) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            incident_id,
            row["signal_key"],
            row["source"],
            row["signal"],
            row["component"],
            Jsonb(row["labels"]),
            row["severity"],
            row["first_seen"],
            row["last_seen"],
            row["occurrences"],
            row["active"],
        ),
    )


def _hypothesis_summary(h: dict) -> dict:
    return {"root": h.get("suspected_root_component"), "cause": h.get("suspected_cause")}


def _update_incident(
    cur: psycopg.Cursor, row: dict, draft: IncidentDraft, now: datetime, actor: str
) -> Counter:
    changes: Counter = Counter()
    incident_id = row["platform_incident_id"]
    cur.execute(
        "SELECT signal_key, active, occurrences, last_seen, severity FROM platform_incident_signals "
        "WHERE platform_incident_id = %s",
        (incident_id,),
    )
    stored = {r["signal_key"]: r for r in cur.fetchall()}
    wanted = {s.key: s for s in draft.signals}
    for key, s in wanted.items():
        old = stored.get(key)
        if old is None:
            _insert_signal(cur, incident_id, s)
            _event(
                cur,
                incident_id,
                now,
                "signal_added",
                actor,
                {"signal": key, "component": s.component, "source": s.source},
            )
            changes["signal_added"] += 1
            continue
        if old["active"] and not s.active:
            _event(cur, incident_id, now, "signal_cleared", actor, {"signal": key})
            changes["signal_cleared"] += 1
        elif not old["active"] and s.active:
            _event(
                cur,
                incident_id,
                now,
                "signal_reactivated",
                actor,
                {"signal": key, "occurrences": s.occurrences},
            )
            changes["signal_reactivated"] += 1
        if (old["active"], old["occurrences"], old["last_seen"], old["severity"]) != (
            s.active,
            s.occurrences,
            s.last_seen,
            s.severity,
        ):
            cur.execute(
                "UPDATE platform_incident_signals SET active = %s, occurrences = %s, last_seen = %s, severity = %s "
                "WHERE platform_incident_id = %s AND signal_key = %s",
                (s.active, s.occurrences, s.last_seen, s.severity, incident_id, key),
            )
    for key, old in stored.items():
        if key not in wanted and old["active"]:
            cur.execute(
                "UPDATE platform_incident_signals SET active = false WHERE platform_incident_id = %s AND signal_key = %s",
                (incident_id, key),
            )
            _event(
                cur,
                incident_id,
                now,
                "signal_cleared",
                actor,
                {"signal": key, "reason": "regrouped into another incident"},
            )
            changes["signal_regrouped"] += 1
    if row["severity"] != draft.severity:
        _event(
            cur,
            incident_id,
            now,
            "severity_changed",
            actor,
            {"from": row["severity"], "to": draft.severity},
        )
        changes["severity_changed"] += 1
    before, after = _hypothesis_summary(row["hypothesis"]), _hypothesis_summary(draft.hypothesis)
    if before != after:
        _event(cur, incident_id, now, "hypothesis_changed", actor, {"from": before, "to": after})
        changes["hypothesis_changed"] += 1
    total = len(wanted) + sum(1 for k in stored if k not in wanted)
    cur.execute(
        "UPDATE platform_incidents SET severity = %s, title = %s, components = %s, hypothesis = %s, signal_count = %s, "
        "updated_at = %s WHERE platform_incident_id = %s",
        (
            draft.severity,
            draft.title,
            draft.components,
            Jsonb(draft.hypothesis),
            max(total, 1),
            max(now, row["opened_at"]),
            incident_id,
        ),
    )
    return changes


def sync(
    conn: psycopg.Connection, drafts: list[IncidentDraft], now: datetime, actor: str = ACTOR
) -> Counter:
    """Bring the stored incidents in line with `drafts` at `now`. Returns counts of what happened."""
    result: Counter = Counter()
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM platform_incidents WHERE status <> 'resolved' FOR UPDATE")
        live = {r["incident_key"]: r for r in cur.fetchall()}
        cur.execute(
            "SELECT DISTINCT ON (incident_key) * FROM platform_incidents WHERE status = 'resolved' AND resolved_at >= %s "
            "ORDER BY incident_key, resolved_at DESC",
            (now - timedelta(seconds=REOPEN_WITHIN_S),),
        )
        recent = {r["incident_key"]: r for r in cur.fetchall()}
        by_key = {d.key: d for d in drafts}
        claimed: dict[
            str, str
        ] = {}  # signal key -> the draft key that now holds it, for every active signal
        for d in drafts:
            for s in d.signals:
                if s.active:
                    claimed[s.key] = d.key

        for d in drafts:
            row = live.get(d.key)
            if row is None:
                if not d.active:
                    continue
                old = recent.get(d.key)
                if old is not None:
                    cur.execute(
                        "UPDATE platform_incidents SET status = 'reopened', resolved_at = NULL WHERE platform_incident_id = %s",
                        (old["platform_incident_id"],),
                    )
                    _event(
                        cur,
                        old["platform_incident_id"],
                        now,
                        "reopened",
                        actor,
                        {"components": d.components},
                    )
                    old["status"], old["resolved_at"] = "reopened", None
                    result["reopened"] += 1
                    changes = _update_incident(cur, old, d, now, actor)
                else:
                    incident_id = uuid.uuid4()
                    cur.execute(
                        "INSERT INTO platform_incidents (platform_incident_id, incident_key, status, severity, title, components, "
                        "opened_at, updated_at, hypothesis, signal_count) VALUES (%s, %s, 'open', %s, %s, %s, %s, %s, %s, %s)",
                        (
                            incident_id,
                            d.key,
                            d.severity,
                            d.title,
                            d.components,
                            min(d.opened_at, now),
                            now,
                            Jsonb(d.hypothesis),
                            len(d.signals),
                        ),
                    )
                    for s in d.signals:
                        _insert_signal(cur, incident_id, s)
                    _event(
                        cur,
                        incident_id,
                        now,
                        "opened",
                        actor,
                        {
                            "root_component": d.key,
                            "components": d.components,
                            "signals": [s.key for s in d.signals],
                            "hypothesis_verified": False,
                        },
                    )
                    result["opened"] += 1
                    continue
            else:
                changes = _update_incident(cur, row, d, now, actor)
            result["updated"] += 1 if sum(changes.values()) else 0
            result.update({f"event_{k}": v for k, v in changes.items()})

        for key, row in live.items():
            d = by_key.get(key)
            if d is not None and d.active:
                continue
            cur.execute(
                "SELECT signal_key FROM platform_incident_signals WHERE platform_incident_id = %s AND active",
                (row["platform_incident_id"],),
            )
            active_keys = [r["signal_key"] for r in cur.fetchall()]
            moved = sorted({claimed[k] for k in active_keys if k in claimed} - {key})
            all_moved = (
                d is None
                and bool(active_keys)
                and all(claimed.get(k, key) != key for k in active_keys)
            )
            quiet = d.quiet_for_s if d is not None else (now - row["updated_at"]).total_seconds()
            if quiet < RESOLVE_AFTER_S and not all_moved:
                continue
            cur.execute(
                "UPDATE platform_incident_signals SET active = false WHERE platform_incident_id = %s AND active",
                (row["platform_incident_id"],),
            )
            cur.execute(
                "UPDATE platform_incidents SET status = 'resolved', resolved_at = %s, updated_at = %s "
                "WHERE platform_incident_id = %s",
                (now, max(now, row["opened_at"]), row["platform_incident_id"]),
            )
            _event(
                cur,
                row["platform_incident_id"],
                now,
                "resolved",
                actor,
                {
                    "auto": True,
                    "quiet_for_s": round(quiet),
                    **({"merged_into": moved} if moved else {}),
                },
            )
            result["resolved"] += 1
    return result


# ---------------------------------------------------------------------------------------------------------- reading
def list_incidents(conn: psycopg.Connection, status: str | None = None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM platform_incidents WHERE (%s::text IS NULL OR status = %s) ORDER BY opened_at, incident_key",
            (status, status),
        )
        return cur.fetchall()


def signals_of(conn: psycopg.Connection, incident_id: uuid.UUID) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM platform_incident_signals WHERE platform_incident_id = %s ORDER BY first_seen, signal_key",
            (incident_id,),
        )
        return cur.fetchall()


def timeline(conn: psycopg.Connection, incident_id: uuid.UUID) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, at, event, actor, detail FROM platform_incident_events WHERE platform_incident_id = %s ORDER BY id",
            (incident_id,),
        )
        return cur.fetchall()


# ------------------------------------------------------------------------------------------- what a person may do
def transition(
    conn: psycopg.Connection,
    incident_id: uuid.UUID,
    to_status: str,
    actor: str,
    note: str = "",
    at: datetime | None = None,
) -> None:
    at = at or datetime.now().astimezone()
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT status FROM platform_incidents WHERE platform_incident_id = %s FOR UPDATE",
            (incident_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise IncidentError("no such platform incident")
        if to_status not in ALLOWED_TRANSITIONS[row["status"]]:
            raise IncidentError(f"{row['status']} -> {to_status} is not allowed")
        cur.execute(
            "UPDATE platform_incidents SET status = %s, updated_at = %s, resolved_at = %s WHERE platform_incident_id = %s",
            (to_status, at, at if to_status == "resolved" else None, incident_id),
        )
        _event(cur, incident_id, at, to_status, actor, {"note": note, "from": row["status"]})


def record_verified_cause(
    conn: psycopg.Connection,
    incident_id: uuid.UUID,
    cause: str,
    actor: str,
    at: datetime | None = None,
) -> None:
    """The one place a cause becomes fact: a named person states it. Not available to the correlator's database role."""
    at = at or datetime.now().astimezone()
    if not cause.strip():
        raise IncidentError("a verified cause needs text")
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "UPDATE platform_incidents SET verified_cause = %s, updated_at = %s WHERE platform_incident_id = %s",
            (cause, at, incident_id),
        )
        if cur.rowcount != 1:
            raise IncidentError("no such platform incident")
        _event(cur, incident_id, at, "cause_verified", actor, {"cause": cause})
