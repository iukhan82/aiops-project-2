"""P06.07 acceptance evidence, against the real Postgres:

    python source-code/backend/analytics/verify_incidents.py

Part A replays a held-out blockage run through the real detectors and the
correlator in 5-minute simulated ticks: duplicates group, hypotheses stay
hypotheses, escalation and resolution follow the evidence, the API serves
contract-valid incidents. Part B exercises what real data does not produce on
demand (a bridging merge, reopen, escalation rules, an operator-owned incident,
the watch list) with *synthetic candidates, labelled as such*, in the same
database through the same code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import uvicorn
from jsonschema import Draft202012Validator

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics import congestion_service as cs  # noqa: E402
from backend.analytics import incident_service as inc  # noqa: E402
from backend.analytics import safety_service as ss  # noqa: E402
from backend.analytics.correlation import Topology, link, load_policy, view_at  # noqa: E402
from backend.analytics.evaluate_congestion import DATASET  # noqa: E402
from backend.analytics.forecast_data import read_events  # noqa: E402
from backend.analytics.kpi_service import load_segments  # noqa: E402

os.environ.setdefault(
    "AIOPS_AUTH_MODE", "off"
)  # this script tests data, not authentication; verify_auth.py proves the latter
from backend.api.app import app  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.loader.platform_loader import load_events, register_devices, reset_simulated_loops  # noqa: E402
from backend.repositories.incidents import transition_incident  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402

GEOMETRY = "2026-09-18.1"
RUN = "test/p06-test-s20260925-am-peak-blockage"
SCEN = SOURCE_ROOT / "simulator" / "scenarios" / "output" / "run-a"
SENSORS = SOURCE_ROOT / "simulator" / "sensors" / "output" / "run-a"
SCHEMA = json.loads(
    (SOURCE_ROOT / "contracts" / "incident" / "v1" / "schema.json").read_text(encoding="utf-8")
)
T0 = datetime.fromisoformat("2026-09-18T09:00:00+00:00")
HOST, PORT = "127.0.0.1", 8798
SYNTH = "test:synthetic"
ev = Evidence("P06.07")


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


def purge(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM incidents WHERE incident_id IN (SELECT incident_id FROM incident_transitions WHERE from_status IS NULL AND changed_by = 'system:correlator')"
        )
        cur.execute("DELETE FROM detection_candidates WHERE source = %s", (SYNTH,))
    conn.commit()


def snapshot(conn: psycopg.Connection) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT incident_id, status, severity, confidence, updated_at, resolved_at, evidence_cleared_at, duplicate_of, incident_type FROM incidents ORDER BY incident_id"
        )
        a = cur.fetchall()
        cur.execute("SELECT count(*) FROM incident_transitions")
        b = cur.fetchone()
        cur.execute(
            "SELECT incident_id, candidate_id, relation FROM incident_candidates ORDER BY candidate_id"
        )
        c = cur.fetchall()
        cur.execute(
            "SELECT incident_id, rank, hypothesis FROM incident_hypotheses ORDER BY incident_id, rank"
        )
        d = cur.fetchall()
    return hashlib.sha256(json.dumps([a, b, c, d], default=str).encode()).hexdigest()


def status_of(conn: psycopg.Connection, incident_id: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM incidents WHERE incident_id = %s", (incident_id,))
        return cur.fetchone()[0]


def add_candidate(
    conn: psycopg.Connection,
    kind: str,
    element: str,
    etype: str,
    onset_s: int,
    clear_s: int | None,
    base: datetime,
    evidence_id: str,
    severity: str = "medium",
    corroborated: bool = False,
    tag: str = "",
) -> str:
    cid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic:{kind}:{element}:{onset_s}:{tag}"))
    onset = base + timedelta(seconds=onset_s)
    cs.store_candidates(
        conn,
        [
            {
                "candidate_id": cid,
                "kind": kind,
                "network_element_type": etype,
                "network_element_id": element,
                "geometry_version": GEOMETRY,
                "onset_time": onset,
                "clear_time": None if clear_s is None else base + timedelta(seconds=clear_s),
                "detected_at": onset + timedelta(seconds=60),
                "severity": severity,
                "confidence": 0.9,
                "evidence_event_ids": [evidence_id],
                "source": SYNTH,
                "attributes": {"corroborated": corroborated},
            }
        ],
    )
    return cid


def incident_of(conn: psycopg.Connection, candidate_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT incident_id FROM incident_candidates WHERE candidate_id = %s", (candidate_id,)
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def history(conn: psycopg.Connection, incident_id: str) -> list[tuple[str | None, str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT from_status, to_status, note FROM incident_transitions WHERE incident_id = %s ORDER BY changed_at, id",
            (incident_id,),
        )
        return cur.fetchall()


async def api_checks(known_incident: str, first_duplicate: str | None) -> None:
    server = run_server()
    try:
        async with httpx.AsyncClient(base_url=f"http://{HOST}:{PORT}", timeout=30) as client:
            page = (await client.get("/api/v1/incidents", params={"limit": 500})).json()
            validator = Draft202012Validator(SCHEMA)
            errors = [e.message for item in page["items"] for e in validator.iter_errors(item)]
            ev.check(
                "api_incidents_are_incident_v1_contract_valid",
                bool(page["items"]) and not errors,
                detail=f"{len(page['items'])} records; {errors[:1]}",
            )
            ev.check(
                "api_never_reports_a_verified_cause_for_a_correlator_incident",
                all("verified_cause" not in i for i in page["items"]),
            )
            with_dups = (
                await client.get(
                    "/api/v1/incidents", params={"limit": 500, "include_duplicates": True}
                )
            ).json()["items"]
            ev.check(
                "api_hides_merged_duplicates_unless_asked",
                len(with_dups) >= len(page["items"])
                and all("duplicate_of" not in i for i in page["items"]),
            )
            filtered = (
                await client.get("/api/v1/incidents", params={"status": "resolved", "limit": 500})
            ).json()["items"]
            ev.check("api_status_filter_works", all(i["status"] == "resolved" for i in filtered))
            first = (await client.get("/api/v1/incidents", params={"limit": 2})).json()
            second = (
                (
                    await client.get(
                        "/api/v1/incidents", params={"limit": 2, "cursor": first["next_cursor"]}
                    )
                ).json()
                if first["next_cursor"]
                else {"items": []}
            )
            ev.check(
                "api_cursor_pagination_has_no_overlap",
                not (
                    {i["incident_id"] for i in first["items"]}
                    & {i["incident_id"] for i in second["items"]}
                ),
            )
            detail = (await client.get(f"/api/v1/incidents/{known_incident}")).json()
            ev.check(
                "api_detail_explains_the_incident",
                {"incident", "candidates", "hypotheses", "transitions", "evidence_sources"}
                <= set(detail)
                and detail["candidates"]
                and detail["hypotheses"]
                and detail["transitions"][0]["to_status"] == "open"
                and all("not verified" in h["hypothesis"] for h in detail["hypotheses"])
                and {c["relation"] for c in detail["candidates"]} & {"primary"},
                detail=f"{len(detail['candidates'])} candidates, {len(detail['hypotheses'])} hypotheses, {len(detail['transitions'])} transitions",
            )
            ev.check(
                "api_unknown_or_malformed_incident_is_404",
                (await client.get(f"/api/v1/incidents/{uuid.uuid4()}")).status_code == 404
                and (await client.get("/api/v1/incidents/not-a-uuid")).status_code == 404,
            )
            if first_duplicate:
                dup = (await client.get(f"/api/v1/incidents/{first_duplicate}")).json()
                ev.check(
                    "api_merged_duplicate_points_at_the_survivor",
                    dup["incident"]["status"] == "resolved" and dup["incident"]["duplicate_of"],
                )
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)


def main() -> int:
    policy = load_policy()
    devices = jsonl(DATASET / "devices.jsonl") + jsonl(SENSORS / "devices.jsonl")
    events = read_events(DATASET / RUN / "events.jsonl")
    end = T0 + timedelta(hours=3)
    with psycopg.connect(dsn_from_env()) as conn:
        register_devices(conn, devices)
        reset_simulated_loops(conn)
        purge(conn)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM observation_events WHERE device_id LIKE 'road-condition-%' OR device_id LIKE 'weather-station-%' OR device_id LIKE 'signal-%'"
            )
        conn.commit()
        load_events(conn, events)
        load_events(conn, jsonl(SCEN / "overlay_events.jsonl"))

        # ---- Part A: real detectors, replayed in 5-minute ticks ----
        cs.detect_and_store(conn, T0, end, GEOMETRY)
        ss.detect_stall(conn, T0, end, GEOMETRY, DATASET / "devices.jsonl")
        ss.detect_overlays(conn, T0, T0 + timedelta(minutes=20), GEOMETRY)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT kind, count(*) FROM detection_candidates WHERE source <> %s GROUP BY kind ORDER BY kind",
                (SYNTH,),
            )
            kinds = dict(cur.fetchall())
        n_candidates = sum(kinds.values())
        reports = []
        t = T0
        while t <= end + timedelta(minutes=30):
            reports.append((t, inc.sync(conn, t, GEOMETRY, policy)))
            t += timedelta(minutes=5)
        totals: dict[str, int] = {}
        for _, r in reports:
            for k, v in r.items():
                totals[k] = totals.get(k, 0) + v
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM incidents WHERE incident_id IN (SELECT incident_id FROM incident_transitions WHERE from_status IS NULL AND changed_by = 'system:correlator')"
            )
            (n_incidents,) = cur.fetchone()
            cur.execute("SELECT count(*) FROM incident_candidates")
            (n_linked,) = cur.fetchone()
        ev.check(
            "real_detectors_produced_candidates_of_several_kinds",
            len(kinds) >= 3,
            detail=str(kinds),
        )
        ev.check(
            "correlation_reduces_candidates_to_fewer_incidents",
            0 < n_incidents < n_candidates,
            detail=f"{n_candidates} candidates -> {n_incidents} incidents, {n_linked} linked, totals {totals}",
        )

        # every pair the rules would connect ended up in the same incident (checked on the final knowledge state)
        final = end + timedelta(minutes=30)
        cands = [
            v
            for c in inc.fetch_candidates(conn, GEOMETRY, final)
            if (v := view_at(c, final)) is not None
        ]
        topo = Topology(load_segments(conn, GEOMETRY), policy.max_hops)
        links = inc._load_links(conn)
        split = [
            (a.kind, b.kind)
            for i, a in enumerate(cands)
            for b in cands[i + 1 :]
            if link(a, b, topo, policy, final)
            and a.candidate_id in links
            and b.candidate_id in links
            and links[a.candidate_id] != links[b.candidate_id]
        ]
        # a pair may legitimately be split only when one incident was merged away (its duplicate_of points at the other)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT incident_id, duplicate_of FROM incidents WHERE duplicate_of IS NOT NULL"
            )
            merged = {str(a): str(b) for a, b in cur.fetchall()}
        unexplained = [
            p for p in split if not (links.get(p[0]) in merged or links.get(p[1]) in merged)
        ]
        ev.check(
            "no_two_candidates_the_rules_connect_are_left_in_different_incidents",
            not unexplained,
            detail=f"{len(cands)} candidates checked",
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM incident_candidates WHERE relation = 'duplicate_source'"
            )
            (n_dup,) = cur.fetchone()
            cur.execute("SELECT count(*) FROM incident_candidates WHERE relation = 'consequence'")
            (n_cons,) = cur.fetchone()
        ev.check(
            "duplicate_sources_and_consequences_are_recorded_as_such",
            n_dup + n_cons > 0,
            detail=f"{n_dup} duplicate sources, {n_cons} consequences",
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM incidents WHERE incident_id IN (SELECT incident_id FROM incident_candidates) AND incident_id NOT IN "
                "(SELECT incident_id FROM incident_hypotheses) AND duplicate_of IS NULL"
            )
            (no_hyp,) = cur.fetchone()
            cur.execute(
                "SELECT count(*) FROM incidents WHERE verified_cause IS NOT NULL AND incident_id IN (SELECT incident_id FROM incident_candidates)"
            )
            (verified,) = cur.fetchone()
            cur.execute(
                "SELECT count(*) FROM incidents WHERE incident_id IN (SELECT incident_id FROM incident_candidates) AND root_cause_hypothesis IS NOT NULL "
                "AND root_cause_hypothesis NOT LIKE '%%hypothesis, not verified%%'"
            )
            (unlabelled,) = cur.fetchone()
            cur.execute(
                "SELECT DISTINCT incident_type, owner_role FROM incidents WHERE incident_id IN (SELECT incident_id FROM incident_candidates) ORDER BY 1"
            )
            owners = cur.fetchall()
        ev.check(
            "every_live_incident_has_ranked_hypotheses_and_none_claims_a_verified_cause",
            no_hyp == 0 and verified == 0 and unlabelled == 0,
            detail=str(owners),
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_status, count(*) FROM incident_transitions GROUP BY to_status ORDER BY 1"
            )
            trans = dict(cur.fetchall())
        ev.check(
            "incidents_resolved_themselves_once_evidence_cleared",
            trans.get("resolved", 0) > 0,
            detail=str(trans),
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT min(changed_at), max(changed_at) FROM incident_transitions WHERE changed_by = 'system:correlator'"
            )
            lo, hi = cur.fetchone()
        ev.check(
            "transition_times_lie_on_the_simulated_timeline",
            T0 <= lo and hi <= end + timedelta(hours=1),
            detail=f"{lo} .. {hi}",
        )

        # how much of the measured truth do the incidents cover? (informational; P06.08 evaluates properly)
        truth = [
            json.loads(line)
            for line in (DATASET / RUN / "incidents.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        covered = 0
        with conn.cursor() as cur:
            for i in truth:
                if not i["valid"]:
                    continue
                cur.execute(
                    "SELECT count(*) FROM incident_candidates ic JOIN detection_candidates c USING (candidate_id) WHERE c.network_element_id = %s AND c.kind IN ('stalled_vehicle','congestion','spillback')",
                    (i["edge_id"],),
                )
                covered += cur.fetchone()[0] > 0
        ev.metrics["measured_blockages_with_a_linked_candidate_on_their_segment"] = {
            "covered": covered,
            "of": sum(1 for i in truth if i["valid"]),
        }

        before = snapshot(conn)
        again = inc.sync(conn, final, GEOMETRY, policy)
        ev.check(
            "resync_at_the_same_time_changes_nothing",
            snapshot(conn) == before
            and not any(
                again.get(k)
                for k in ("opened", "merged", "reopened", "resolved", "escalated", "updated")
            ),
            detail=str(again),
        )
        ev.metrics["replay"] = {
            "ticks": len(reports),
            "candidates_by_kind": kinds,
            "incidents": n_incidents,
            "totals": totals,
        }

        with conn.cursor() as cur:
            cur.execute(
                "SELECT incident_id FROM incidents WHERE duplicate_of IS NULL AND incident_id IN (SELECT incident_id FROM incident_hypotheses) ORDER BY opened_at LIMIT 1"
            )
            known = str(cur.fetchone()[0])
            cur.execute("SELECT incident_id FROM incidents WHERE duplicate_of IS NOT NULL LIMIT 1")
            dup_row = cur.fetchone()

        # ---- Part B: synthetic candidates, same database, same code ----
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (d.device_type) d.device_type, e.event_id FROM observation_events e JOIN devices d USING (device_id) ORDER BY d.device_type, e.event_id"
            )
            sample = {t: str(i) for t, i in cur.fetchall()}
        loop_ev, road_ev = sample["inductive_loop"], sample["road_condition_sensor"]
        base = T0 + timedelta(
            hours=5
        )  # every sub-scenario gets its own hour: no cross-talk whatever the geometry

        def hour(n: int) -> datetime:
            return base + timedelta(hours=n)

        def tick_at(when: datetime) -> dict:
            return inc.sync(conn, when, GEOMETRY, policy)

        # B1 bridge merge: two stalls two segments apart become two incidents; a queue between them joins them
        b1 = hour(0)
        s2 = add_candidate(
            conn,
            "stalled_vehicle",
            "int-a2_int-a3",
            "segment",
            0,
            900,
            b1,
            loop_ev,
            "high",
            True,
            "b1a",
        )
        s3 = add_candidate(
            conn,
            "stalled_vehicle",
            "int-a3_int-a4",
            "segment",
            20,
            900,
            b1,
            loop_ev,
            "high",
            True,
            "b1b",
        )
        tick_at(b1 + timedelta(seconds=200))
        i2, i3 = incident_of(conn, s2), incident_of(conn, s3)
        ev.check("B1_two_unrelated_blockages_start_as_two_incidents", i2 and i3 and i2 != i3)
        bridge = add_candidate(
            conn,
            "congestion",
            "int-a2_int-a3",
            "segment",
            240,
            900,
            b1,
            loop_ev,
            "high",
            False,
            "b1c",
        )
        rep = tick_at(b1 + timedelta(seconds=400))
        i2b, i3b, ib = incident_of(conn, s2), incident_of(conn, s3), incident_of(conn, bridge)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT incident_id, duplicate_of, status FROM incidents WHERE incident_id = ANY(%s::uuid[])",
                ([i2, i3],),
            )
            rows = {str(a): (str(b) if b else None, c) for a, b, c in cur.fetchall()}
        ev.check(
            "B1_a_bridging_candidate_merges_them_keeping_the_older_incident_and_marking_the_other_duplicate",
            i2b == i3b == ib
            and rep.get("merged") == 1
            and sorted(v[0] is None for v in rows.values()) == [False, True]
            and any(v[1] == "resolved" and v[0] for v in rows.values()),
            detail=str(rows),
        )

        # B2 resolution and reopen
        b2 = hour(1)
        c1 = add_candidate(
            conn,
            "congestion",
            "int-c1_int-c2",
            "segment",
            0,
            400,
            b2,
            loop_ev,
            "medium",
            False,
            "b2a",
        )
        tick_at(b2 + timedelta(seconds=200))
        i = incident_of(conn, c1)
        ev.check("B2_a_confident_candidate_opens_an_incident", i and status_of(conn, i) == "open")
        tick_at(b2 + timedelta(seconds=450))
        ev.check(
            "B2_it_stays_open_until_the_evidence_has_been_clear_for_the_hysteresis",
            status_of(conn, i) == "open",
        )
        tick_at(b2 + timedelta(seconds=400 + 130))
        ev.check("B2_it_resolves_itself_after_the_hysteresis", status_of(conn, i) == "resolved")
        c1b = add_candidate(
            conn,
            "congestion",
            "int-c1_int-c2",
            "segment",
            560,
            1500,
            b2,
            loop_ev,
            "medium",
            False,
            "b2b",
        )
        tick_at(b2 + timedelta(seconds=700))
        ev.check(
            "B2_new_evidence_after_resolution_reopens_the_same_incident",
            incident_of(conn, c1b) == i and status_of(conn, i) == "reopened",
            detail=str([h[:2] for h in history(conn, i)]),
        )

        # B3 escalation rules
        b3 = hour(2)
        hi = add_candidate(
            conn,
            "congestion",
            "int-b1_int-b2",
            "segment",
            0,
            3000,
            b3,
            loop_ev,
            "high",
            False,
            "b3a",
        )
        tick_at(b3 + timedelta(seconds=300))
        ihi = incident_of(conn, hi)
        ev.check(
            "B3_a_high_severity_incident_is_not_escalated_immediately",
            status_of(conn, ihi) == "open",
        )
        tick_at(b3 + timedelta(seconds=300 + 640))
        ev.check(
            "B3_unacknowledged_high_severity_escalates_after_the_policy_delay",
            status_of(conn, ihi) == "escalated",
            detail=str(history(conn, ihi)[-1]),
        )
        b3b = hour(3)
        crit = add_candidate(
            conn,
            "collision",
            "int-b3_int-b4",
            "segment",
            0,
            3000,
            b3b,
            loop_ev,
            "critical",
            False,
            "b3b",
        )
        tick_at(b3b + timedelta(seconds=200))
        ev.check(
            "B3_a_critical_incident_escalates_at_once",
            status_of(conn, incident_of(conn, crit)) == "escalated",
        )
        b3c = hour(4)
        fl = add_candidate(
            conn, "flooding", "corridor-c", "corridor", 0, 3000, b3c, road_ev, "high", False, "b3c"
        )
        co = add_candidate(
            conn,
            "congestion",
            "int-c2_int-c3",
            "segment",
            30,
            3000,
            b3c,
            loop_ev,
            "high",
            False,
            "b3d",
        )
        tick_at(b3c + timedelta(seconds=200))
        ifl = incident_of(conn, fl)
        ev.check(
            "B3_two_independent_sensor_modalities_escalate_a_high_severity_incident_immediately",
            ifl == incident_of(conn, co)
            and status_of(conn, ifl) == "escalated"
            and "independent" in history(conn, ifl)[-1][2],
            detail=str(history(conn, ifl)[-1]),
        )

        # B4 operator-owned incident is never auto-resolved
        b4 = hour(5)
        own = add_candidate(
            conn,
            "congestion",
            "int-a1_int-a2",
            "segment",
            0,
            300,
            b4,
            loop_ev,
            "medium",
            False,
            "b4a",
        )
        tick_at(b4 + timedelta(seconds=200))
        iown = incident_of(conn, own)
        transition_incident(
            conn, iown, "acknowledged", "operator:demo", "taking it", at=b4 + timedelta(seconds=210)
        )
        tick_at(b4 + timedelta(seconds=900))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, evidence_cleared_at FROM incidents WHERE incident_id = %s", (iown,)
            )
            st, cleared = cur.fetchone()
        ev.check(
            "B4_an_acknowledged_incident_is_not_auto_resolved_but_records_that_its_evidence_cleared",
            st == "acknowledged" and cleared is not None,
        )
        transition_incident(
            conn,
            iown,
            "resolved",
            "operator:demo",
            "confirmed clear",
            at=b4 + timedelta(seconds=950),
        )
        tick_at(b4 + timedelta(seconds=1000))
        ev.check("B4_an_operator_resolution_stands", status_of(conn, iown) == "resolved")

        # B5 watch list
        b5 = hour(6)
        lone = add_candidate(
            conn,
            "pedestrian_conflict",
            "int-b2",
            "intersection",
            0,
            300,
            b5,
            loop_ev,
            "medium",
            False,
            "b5a",
        )
        rep = tick_at(b5 + timedelta(seconds=400))
        ev.check(
            "B5_a_low_precision_candidate_alone_stays_on_the_watch_list",
            incident_of(conn, lone) is None and rep.get("watch_list_groups", 0) >= 1,
            detail=str(rep),
        )

        # B6 operator transitions still obey P05.08's state machine
        try:
            transition_incident(conn, iown, "investigating", "operator:demo", "should fail")
            illegal = False
        except Exception:  # noqa: BLE001
            illegal = True
        ev.check("B6_the_repository_state_machine_still_rejects_an_illegal_transition", illegal)

        asyncio.run(api_checks(known, str(dup_row[0]) if dup_row else None))

        purge(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM detection_candidates WHERE source = %s", (SYNTH,))
            ev.check("synthetic_candidates_are_removed_after_the_test", cur.fetchone()[0] == 0)
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
