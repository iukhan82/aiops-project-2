"""P06.07: turn persisted detection candidates into managed incidents.

`sync(conn, now, geometry)` is one correlation tick. It is idempotent (running
it twice at the same `now` changes nothing) and driven by the timeline it is
given, so a live loop passes wall-clock time and a replay passes simulated time.

Lifecycle, all through P05.08's state machine (`transition_incident`), never by
writing `status` directly:
- open        a group whose calibrated confidence reaches the policy's
              `open_confidence` (weaker groups stay candidates, a watch list);
- update      severity/confidence/evidence/hypotheses follow the group;
- merge       a group that bridges two incidents keeps the older one, relinks
              the other's candidates and resolves it with `duplicate_of`;
- escalate    a high-severity incident nobody has acknowledged for
              `escalate_unacknowledged_after_s`, a critical one at once, or one
              independently corroborated by a second sensor modality;
- resolve     automatically only for incidents nobody has taken (open/reopened)
              once *all* evidence has been clear for `resolve_hysteresis_s`;
              an acknowledged/investigating/escalated incident only records
              `evidence_cleared_at` - a person closes it;
- reopen      new evidence after resolution.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.correlation import (  # noqa: E402
    DEVICE_MODALITY,
    OWNER_ROLE,
    SEVERITIES,
    Cand,
    Group,
    Policy,
    Topology,
    correlate,
    group_confidence,
    group_severity,
    hypotheses,
    load_policy,
    view_at,
)
from backend.analytics.kpi_service import load_segments  # noqa: E402
from backend.observability import traced  # noqa: E402
from backend.repositories.incidents import create_incident, transition_incident  # noqa: E402

ACTOR = "system:correlator"
STALL_CLEAR_AFTER_S = (
    120.0  # stall candidates carry no clear time: the edge model simply stops alarming
)


def fetch_candidates(conn: psycopg.Connection, geometry: str, until: datetime) -> list[Cand]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.candidate_id, c.kind, c.network_element_type, c.network_element_id, c.onset_time, c.clear_time, c.detected_at,
                   c.severity, c.confidence, c.source, c.attributes, c.evidence_event_ids,
                   (SELECT d.device_type FROM observation_events e JOIN devices d ON d.device_id = e.device_id
                    WHERE e.event_id = ANY(c.evidence_event_ids) GROUP BY d.device_type ORDER BY count(*) DESC, d.device_type LIMIT 1),
                   (SELECT max(e.observation_time) FROM observation_events e WHERE e.event_id = ANY(c.evidence_event_ids))
            FROM detection_candidates c WHERE c.geometry_version = %s AND c.detected_at <= %s
            ORDER BY c.onset_time, c.candidate_id
            """,
            (geometry, until),
        )
        rows = cur.fetchall()
    out = []
    for cid, kind, et, eid, onset, clear, det, sev, conf, src, attrs, ev, dev, seen in rows:
        attrs = attrs or {}
        if kind == "stalled_vehicle" and clear is None and attrs.get("last_alarm_at"):
            last_alarm = datetime.fromisoformat(attrs["last_alarm_at"])
            clear = last_alarm + timedelta(seconds=STALL_CLEAR_AFTER_S)
            seen = max(seen, last_alarm) if seen else last_alarm
        seen = (
            max(seen, det) if seen else det
        )  # a candidate was necessarily still alive when it was detected
        out.append(
            Cand(
                str(cid),
                kind,
                et,
                eid,
                onset,
                clear,
                det,
                sev,
                conf,
                src,
                DEVICE_MODALITY.get(dev or "", dev or "unknown"),
                bool(attrs.get("corroborated", False)),
                tuple(str(x) for x in ev),
                attrs,
                seen,
            )
        )
    return out


def _spec(group: Group, policy: Policy, topo: Topology) -> dict:
    primary = group.primary
    hyp = hypotheses(group, policy, topo)
    evidence = list(dict.fromkeys(e for m in group.members for e in m.evidence))
    return {
        "incident_type": primary.kind,
        "severity": group_severity(group),
        "element_type": primary.element_type,
        "element_id": primary.element_id,
        "owner_role": OWNER_ROLE[primary.kind],
        "confidence": group_confidence(group, policy),
        "evidence": evidence,
        "sources": sorted({m.modality for m in group.members}),
        "hypotheses": hyp,
        "top_hypothesis": hyp[0]["hypothesis"] if hyp else None,
        "cleared_at": max(m.end for m in group.members)
        if all(m.end is not None for m in group.members)
        else None,
        "latest_detection": max(m.detected_at for m in group.members),
    }


def _load_links(conn: psycopg.Connection) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT candidate_id, incident_id FROM incident_candidates")
        return {str(c): str(i) for c, i in cur.fetchall()}


def _incident(conn: psycopg.Connection, incident_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, severity, confidence, incident_type, network_element_type, network_element_id, opened_at, resolved_at, "
            "evidence_cleared_at, evidence_sources, root_cause_hypothesis, evidence_event_ids, duplicate_of FROM incidents WHERE incident_id = %s",
            (incident_id,),
        )
        r = cur.fetchone()
    keys = (
        "status",
        "severity",
        "confidence",
        "incident_type",
        "element_type",
        "element_id",
        "opened_at",
        "resolved_at",
        "cleared_at",
        "sources",
        "top_hypothesis",
        "evidence",
        "duplicate_of",
    )
    row = dict(zip(keys, r, strict=True))
    row["evidence"] = [str(x) for x in row["evidence"]]
    return row


def sync(
    conn: psycopg.Connection,
    now: datetime,
    geometry: str,
    policy: Policy | None = None,
    actor: str = ACTOR,
) -> dict:
    with traced("analytics.incidents.sync", correlation_id=f"{geometry}:{now.isoformat()}"):
        policy = policy or load_policy()
        topo = Topology(load_segments(conn, geometry), policy.max_hops)
        visible = [
            v for c in fetch_candidates(conn, geometry, now) if (v := view_at(c, now)) is not None
        ]
        links = _load_links(conn)
        report: Counter = Counter()
        for group in correlate(visible, topo, policy, now):
            spec = _spec(group, policy, topo)
            linked = sorted(
                {links[m.candidate_id] for m in group.members if m.candidate_id in links}
            )
            live = [i for i in linked if _incident(conn, i)["duplicate_of"] is None]
            if not live:
                if spec["confidence"] < policy.open_confidence:
                    report["watch_list_groups"] += 1
                    continue
                incident_id = create_incident(
                    conn,
                    spec["incident_type"],
                    spec["severity"],
                    spec["element_type"],
                    spec["element_id"],
                    geometry,
                    spec["evidence"],
                    spec["owner_role"],
                    spec["confidence"],
                    actor,
                    at=now,
                )
                report["opened"] += 1
            else:
                keep = min(live, key=lambda i: (_incident(conn, i)["opened_at"], i))
                incident_id = keep
                for other in (i for i in live if i != keep):
                    _merge(conn, other, keep, now, actor)
                    report["merged"] += 1
            with traced(
                "analytics.incidents.update_one",
                correlation_id=incident_id,
                severity=spec["severity"],
            ):
                _link(conn, incident_id, group, now)
                report["updated"] += _refresh(conn, incident_id, spec, now)
                _lifecycle(conn, incident_id, group, spec, policy, now, actor, report)
            links.update({m.candidate_id: incident_id for m in group.members})
        return dict(report)


def _merge(conn: psycopg.Connection, other: str, keep: str, now: datetime, actor: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE incident_candidates SET incident_id = %s WHERE incident_id = %s", (keep, other)
        )
        cur.execute("UPDATE incidents SET duplicate_of = %s WHERE incident_id = %s", (keep, other))
    conn.commit()
    if _incident(conn, other)["status"] != "resolved":
        transition_incident(
            conn, other, "resolved", actor, f"merged into {keep}: same event", at=now
        )


def _link(conn: psycopg.Connection, incident_id: str, group: Group, now: datetime) -> None:
    with conn.cursor() as cur:
        for m in group.members:
            cur.execute(
                "INSERT INTO incident_candidates (incident_id, candidate_id, linked_at, relation) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (candidate_id) DO UPDATE SET incident_id = EXCLUDED.incident_id, relation = EXCLUDED.relation",
                (incident_id, m.candidate_id, now, group.relations[m.candidate_id]),
            )
    conn.commit()


def _refresh(conn: psycopg.Connection, incident_id: str, spec: dict, now: datetime) -> int:
    cur_row = _incident(conn, incident_id)
    wanted = {
        "severity": spec["severity"],
        "confidence": spec["confidence"],
        "incident_type": spec["incident_type"],
        "element_type": spec["element_type"],
        "element_id": spec["element_id"],
        "sources": spec["sources"],
        "top_hypothesis": spec["top_hypothesis"],
        "evidence": spec["evidence"],
    }
    changed = any(cur_row[k] != v for k, v in wanted.items())
    with conn.cursor() as cur:
        cur.execute(
            "SELECT hypothesis, likelihood, supporting_candidate_ids FROM incident_hypotheses WHERE incident_id = %s ORDER BY rank",
            (incident_id,),
        )
        stored = [(h, round(lk, 4), sorted(str(x) for x in ids)) for h, lk, ids in cur.fetchall()]
    new = [
        (h["hypothesis"], h["likelihood"], sorted(h["supporting_candidate_ids"]))
        for h in spec["hypotheses"]
    ]
    if stored != new:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM incident_hypotheses WHERE incident_id = %s", (incident_id,))
            for h in spec["hypotheses"]:
                cur.execute(
                    "INSERT INTO incident_hypotheses (incident_id, rank, hypothesis, likelihood, supporting_candidate_ids, generated_at) "
                    "VALUES (%s, %s, %s, %s, %s::uuid[], %s)",
                    (
                        incident_id,
                        h["rank"],
                        h["hypothesis"],
                        h["likelihood"],
                        h["supporting_candidate_ids"],
                        now,
                    ),
                )
        changed = True
    if changed:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE incidents SET severity = %s, confidence = %s, incident_type = %s, network_element_type = %s, network_element_id = %s,
                    owner_role = %s, evidence_sources = %s, root_cause_hypothesis = %s, evidence_event_ids = %s::uuid[],
                    updated_at = GREATEST(updated_at, %s) WHERE incident_id = %s
                """,
                (
                    spec["severity"],
                    spec["confidence"],
                    spec["incident_type"],
                    spec["element_type"],
                    spec["element_id"],
                    spec["owner_role"],
                    spec["sources"],
                    spec["top_hypothesis"],
                    spec["evidence"],
                    now,
                    incident_id,
                ),
            )
    conn.commit()
    return int(changed)


def _lifecycle(
    conn: psycopg.Connection,
    incident_id: str,
    group: Group,
    spec: dict,
    policy: Policy,
    now: datetime,
    actor: str,
    report: Counter,
) -> None:
    row = _incident(conn, incident_id)
    status = row["status"]
    if status == "resolved" and row["duplicate_of"] is None:
        after = row["resolved_at"] is not None and spec["latest_detection"] > row["resolved_at"]
        if after or spec["cleared_at"] is None:
            transition_incident(
                conn, incident_id, "reopened", actor, "new evidence after resolution", at=now
            )
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE incidents SET resolved_at = NULL, evidence_cleared_at = NULL WHERE incident_id = %s",
                    (incident_id,),
                )
            conn.commit()
            report["reopened"] += 1
            status = "reopened"
    if spec["cleared_at"] is None and row["cleared_at"] is not None:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE incidents SET evidence_cleared_at = NULL WHERE incident_id = %s",
                (incident_id,),
            )
        conn.commit()
    if (
        spec["cleared_at"] is not None
        and row["cleared_at"] != spec["cleared_at"]
        and status != "resolved"
    ):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE incidents SET evidence_cleared_at = %s WHERE incident_id = %s",
                (spec["cleared_at"], incident_id),
            )
        conn.commit()
    if (
        status in ("open", "reopened")
        and spec["cleared_at"] is not None
        and now - spec["cleared_at"] >= timedelta(seconds=policy.resolve_hysteresis_s)
    ):
        transition_incident(
            conn,
            incident_id,
            "resolved",
            actor,
            f"all evidence clear since {spec['cleared_at'].isoformat()}",
            at=now,
        )
        report["resolved"] += 1
        return
    severity_rank = SEVERITIES.index(spec["severity"])
    age = (now - row["opened_at"]).total_seconds()
    escalate = None
    if status in ("open", "reopened") and spec["severity"] == "critical":
        escalate = "critical severity"
    elif (
        status in ("open", "reopened")
        and severity_rank >= SEVERITIES.index("high")
        and age >= policy.escalate_unacknowledged_after_s
        and spec["cleared_at"] is None
    ):
        escalate = f"high severity unacknowledged for {int(age)} s"
    elif (
        status in ("open", "reopened", "acknowledged", "investigating")
        and severity_rank >= SEVERITIES.index("high")
        and len(spec["sources"]) >= 2
        and spec["confidence"] >= policy.escalate_corroborated_confidence
        and spec["cleared_at"] is None
    ):
        escalate = "independent sensor modalities agree: " + ", ".join(spec["sources"])
    if escalate:
        transition_incident(conn, incident_id, "escalated", actor, escalate, at=now)
        report["escalated"] += 1
