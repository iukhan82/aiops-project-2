"""P10.07 acceptance evidence: platform signals correlate into incidents; duplicates group; causes stay unverified.

    python source-code/backend/aiops/verify_platform_incidents.py

Needs the running platform (PostgreSQL, Prometheus), the P10.04 evidence with its lab window (run `verify_alerts.py`
first: it records when the faults were switched on and off) and, for the anomaly source, the P10.06 detector package.

A. Structure, in a scratch database. The database itself refuses an incident that is not `inferred`, a hypothesis without
   `"verified": false`, a second live incident for one key and an embedded secret; the correlator's own role can write
   incidents but not `verified_cause`, cannot delete, cannot rewrite or truncate the history and cannot touch traffic,
   command or audit tables; the history is hash-chained and refuses UPDATE and TRUNCATE.
B. Lifecycle rules, in a second scratch database, on small hand-built (SYNTHETIC) signal sets: open, update, auto-resolve;
   a flapping alert reopens instead of opening a new incident (and a late one opens a new incident); a person's
   acknowledgement survives the next sync; a new root that explains an existing incident's signal merges it; a person
   records a verified cause and the machine hypothesis stays labelled unverified.
C. Replay, in a third scratch database, of the REAL timeline the P10.04 lab produced: the `ALERTS` series Prometheus
   recorded and the eight operational signals it stored, stepped through exactly the code the live correlator runs. What
   is measured: alerts in, incidents out, no alert lost, none duplicated across incidents, every incident labelled inferred
   and unverified, every incident resolved after the faults clear, and how many causal links the hypotheses asserted that
   the lab's injection did not actually cause (in the lab every fault was switched on independently, so every link is one
   the injection did not make - reported as what it is, an unverified inference).
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg import errors

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import audit_chain  # noqa: E402
from backend.aiops import correlation as c  # noqa: E402
from backend.aiops import platform_correlator, platform_signals  # noqa: E402
from backend.demo import world  # noqa: E402
from backend.evidence import DOCS_EVIDENCE_DIR, Evidence  # noqa: E402
from backend.repositories import platform_incidents as repo  # noqa: E402
from database import rotate_service_secrets  # noqa: E402
from database.migrate import dsn_from_env, migrate  # noqa: E402

ev = Evidence("P10.07", "p10_07_platform_incidents", docs_name="p10_07_platform_incidents")
UNITS_DB = "aiops_p1007_units"
REPLAY_DB = "aiops_p1007_replay"
ALERT_EVIDENCE = DOCS_EVIDENCE_DIR / "p10_04_alert_rules.json"
SYNTHETIC = datetime(2026, 6, 1, 8, 0, 0, tzinfo=UTC)
ROLE = "svc_platform_correlator"
STEP_S = 15


# ------------------------------------------------------------------------------------------------ scratch databases
def fresh_database(name: str) -> None:
    os.environ["POSTGRES_DB"] = "aiops"
    world.use_database(name)
    if world.database_exists(name):
        world.drop_database(name)
    with psycopg.connect(world._maintenance_dsn(), autocommit=True) as conn:  # noqa: SLF001
        conn.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(dsn_from_env()) as conn:
        migrate(conn)


def admin(name: str) -> psycopg.Connection:
    os.environ["POSTGRES_DB"] = name
    return psycopg.connect(dsn_from_env(), autocommit=True)


def as_correlator(name: str) -> psycopg.Connection:
    os.environ["POSTGRES_DB"] = name
    return psycopg.connect(dsn_from_env(role=ROLE), autocommit=True)


def refused(
    conn: psycopg.Connection, statement: str, params: tuple = (), expect: type = Exception
) -> bool:
    try:
        with conn.transaction():
            conn.execute(statement, params)
    except expect:
        return True
    except psycopg.Error:
        return False
    return False


def insert_incident(conn: psycopg.Connection, key: str, **over) -> None:
    row = {
        "id": str(uuid.uuid4()),
        "key": key,
        "status": "open",
        "truth": "inferred",
        "hyp": '{"verified": false}',
        **over,
    }
    conn.execute(
        "INSERT INTO platform_incidents (platform_incident_id, incident_key, status, severity, title, components, opened_at, "
        "updated_at, hypothesis, signal_count, truth_label) VALUES (%s, %s, %s, 'high', 't', ARRAY['api'], now(), now(), "
        "%s::jsonb, 1, %s)",
        (row["id"], row["key"], row["status"], row["hyp"], row["truth"]),
    )


# ------------------------------------------------------------------------------------------------------- A. structure
def structure_checks() -> None:
    fresh_database(UNITS_DB)
    with admin(UNITS_DB) as conn:
        role = conn.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication FROM pg_roles WHERE rolname = %s",
            (ROLE,),
        ).fetchone()
        ev.check(
            "the_correlator_has_its_own_login_role_that_is_no_superuser_and_cannot_create_databases_or_roles",
            role is not None and not any(role),
            str(role),
        )
        ev.check(
            "a_platform_incident_can_only_be_labelled_inferred",
            refused_insert(conn, "a1", truth="observed"),
        )
        ev.check(
            "the_database_refuses_a_hypothesis_that_is_marked_verified_or_carries_no_verified_flag",
            refused_insert(conn, "a2", hyp='{"verified": true}')
            and refused_insert(conn, "a3", hyp="{}")
            and refused_insert(conn, "a4", hyp='{"verified": "false"}'),
        )
        conn.execute(
            "INSERT INTO platform_incidents (platform_incident_id, incident_key, status, severity, title, components, "
            "opened_at, updated_at, hypothesis, signal_count) VALUES (gen_random_uuid(), 'dup', 'open', 'low', 't', "
            "ARRAY['api'], now(), now(), '{\"verified\": false}', 1)"
        )
        ev.check(
            "the_database_refuses_a_second_live_incident_for_the_same_key",
            refused_insert(conn, "dup", status="acknowledged"),
        )
        ev.check(
            "the_database_refuses_an_embedded_secret_in_a_hypothesis",
            refused_insert(
                conn, "secret", hyp='{"verified": false, "note": "-----BEGIN PRIVATE KEY-----"}'
            ),
        )

    with as_correlator(UNITS_DB) as svc:
        ident = svc.execute("SELECT current_user").fetchone()[0]
        opened = not refused(
            svc,
            "INSERT INTO platform_incidents (platform_incident_id, incident_key, status, severity, title, components, "
            "opened_at, updated_at, hypothesis, signal_count) VALUES (gen_random_uuid(), 'svc-key', 'open', 'low', 't', "
            "ARRAY['api'], now(), now(), '{\"verified\": false}', 1)",
        )
        updated = not refused(
            svc, "UPDATE platform_incidents SET severity = 'medium' WHERE incident_key = 'svc-key'"
        )
        ev.check(
            "the_correlator_connects_as_itself_and_can_write_an_incident_it_inferred",
            ident == ROLE and opened and updated,
            ident,
        )
        ev.check(
            "the_correlator_cannot_write_a_verified_cause_neither_when_opening_nor_after",
            refused(
                svc,
                "INSERT INTO platform_incidents (platform_incident_id, incident_key, status, severity, title, components, "
                "opened_at, updated_at, hypothesis, signal_count, verified_cause) VALUES (gen_random_uuid(), 'x', 'open', "
                "'low', 't', ARRAY['api'], now(), now(), '{\"verified\": false}', 1, 'it was the disk')",
                expect=errors.InsufficientPrivilege,
            )
            and refused(
                svc,
                "UPDATE platform_incidents SET verified_cause = 'it was the disk' WHERE incident_key = 'svc-key'",
                expect=errors.InsufficientPrivilege,
            ),
        )
        ev.check(
            "the_correlator_cannot_delete_an_incident_or_its_history_or_wipe_or_rewrite_the_history",
            refused(svc, "DELETE FROM platform_incidents", expect=errors.InsufficientPrivilege)
            and refused(
                svc, "DELETE FROM platform_incident_events", expect=errors.InsufficientPrivilege
            )
            and refused(
                svc, "TRUNCATE platform_incident_events", expect=errors.InsufficientPrivilege
            )
            and refused(
                svc,
                "UPDATE platform_incident_events SET actor = 'x'",
                expect=errors.InsufficientPrivilege,
            ),
        )
        others = (
            "commands",
            "operator_audit",
            "incidents",
            "observation_events",
            "devices",
            "policy_decisions",
        )
        ev.check(
            "the_correlator_has_no_access_at_all_to_traffic_command_device_or_audit_tables",
            all(
                refused(svc, f"SELECT 1 FROM {t} LIMIT 1", expect=errors.InsufficientPrivilege)
                for t in others
            ),
            ", ".join(others),
        )
    with admin(UNITS_DB) as conn:
        old = ("svc_command_executor", "svc_outcome_verifier", "svc_scenario_control")
        leaked = conn.execute(
            "SELECT grantee, table_name FROM information_schema.role_table_grants WHERE grantee = ANY(%s) "
            "AND table_name LIKE 'platform_incident%%'",
            (list(old),),
        ).fetchall()
        ev.check(
            "the_three_older_service_roles_have_no_access_to_the_platform_incident_tables",
            not leaked,
            str(leaked),
        )
        ev.check(
            "the_history_is_hash_chained_wired_into_the_audit_chain_verifier_and_the_role_is_in_secret_rotation",
            "platform_incident_events" in audit_chain.CHAINED_TABLES
            and ROLE in rotate_service_secrets.ROLES,
        )


def refused_insert(conn: psycopg.Connection, key: str, **over) -> bool:
    try:
        with conn.transaction():
            insert_incident(conn, key, **over)
    except (errors.CheckViolation, errors.UniqueViolation):
        return True
    return False


# ------------------------------------------------------------------------------------------------- B. lifecycle rules
def at(seconds: float) -> datetime:
    return SYNTHETIC + timedelta(seconds=seconds)


def alert(name, onset, last, active=True, severity="warning", **labels):
    return c.alert_signal(
        name,
        labels,
        severity,
        onset=at(onset),
        first_seen=at(onset),
        last_seen=at(last),
        occurrences=1,
        active=active,
    )


def step(svc, signals, now_s):
    return repo.sync(svc, c.correlate(signals, at(now_s)), at(now_s))


def events_of(conn, incident_id) -> list[str]:
    return [e["event"] for e in repo.timeline(conn, incident_id)]


def lifecycle_checks() -> None:  # noqa: PLR0915
    with admin(UNITS_DB) as adm, as_correlator(UNITS_DB) as svc:
        # ---- open, update, auto-resolve
        day = 0
        step(svc, [alert("ApiLatencyP95High", day + 0, day + 60)], day + 60)
        (inc,) = [i for i in repo.list_incidents(adm) if i["incident_key"] == "api"]
        ev.check(
            "an_active_alert_opens_one_incident_that_is_inferred_and_unverified",
            inc["status"] == "open"
            and inc["truth_label"] == "inferred"
            and inc["hypothesis"]["verified"] is False
            and inc["verified_cause"] is None
            and events_of(adm, inc["platform_incident_id"]) == ["opened"],
        )
        step(svc, [alert("ApiLatencyP95High", day + 0, day + 90)], day + 90)
        ev.check(
            "a_repeat_of_the_same_active_alert_adds_no_event_and_no_second_incident",
            len([i for i in repo.list_incidents(adm) if i["incident_key"] == "api"]) == 1
            and events_of(adm, inc["platform_incident_id"]) == ["opened"],
        )
        cleared = alert("ApiLatencyP95High", day + 0, day + 100, active=False)
        step(svc, [cleared], day + 150)
        still_open = repo.list_incidents(adm, "open")
        step(svc, [cleared], day + 100 + c.RESOLVE_AFTER_S + 5)
        resolved = [i for i in repo.list_incidents(adm, "resolved") if i["incident_key"] == "api"]
        ev.check(
            "a_quiet_incident_stays_open_for_the_grace_period_then_resolves_itself",
            any(i["incident_key"] == "api" for i in still_open)
            and len(resolved) == 1
            and resolved[0]["resolved_at"] is not None
            and events_of(adm, inc["platform_incident_id"])
            == ["opened", "signal_cleared", "resolved"],
            str(events_of(adm, inc["platform_incident_id"])),
        )

        # ---- flapping: back inside the reopen window -> the same incident; long after -> a new one
        back = day + 100 + c.RESOLVE_AFTER_S + 5 + 120
        flap = c.alert_signal(
            "ApiLatencyP95High", {}, "warning", onset=at(back), first_seen=at(day), last_seen=at(back + 30),
            occurrences=2, active=True,
        )  # fmt: skip
        step(svc, [flap], back + 30)
        same = [i for i in repo.list_incidents(adm) if i["incident_key"] == "api"]
        ev.check(
            "a_signal_that_returns_soon_after_a_resolve_reopens_the_same_incident_instead_of_opening_a_new_one",
            len(same) == 1
            and same[0]["platform_incident_id"] == inc["platform_incident_id"]
            and same[0]["status"] == "reopened"
            and "reopened" in events_of(adm, inc["platform_incident_id"])
            and "signal_reactivated" in events_of(adm, inc["platform_incident_id"]),
            str(events_of(adm, inc["platform_incident_id"])),
        )
        gone = alert("ApiLatencyP95High", day + 0, back + 30, active=False)
        later = back + 30 + c.RESOLVE_AFTER_S + 5
        step(svc, [gone], later)
        new_start = later + c.REOPEN_WITHIN_S + 300
        fresh = alert("ApiLatencyP95High", new_start, new_start + 30)
        step(svc, [fresh], new_start + 30)
        apis = [i for i in repo.list_incidents(adm) if i["incident_key"] == "api"]
        ev.check(
            "the_same_signal_long_after_a_resolve_opens_a_new_incident",
            len(apis) == 2 and apis[-1]["status"] == "open",
            str([i["status"] for i in apis]),
        )
        step(
            svc,
            [alert("ApiLatencyP95High", new_start, new_start + 30, active=False)],
            new_start + 300,
        )

        # ---- a person's acknowledgement is not overwritten
        day = 20_000
        step(svc, [alert("EdgeModelInactive", day, day + 30)], day + 30)
        (edge,) = [i for i in repo.list_incidents(adm) if i["incident_key"] == "edge-runtime"]
        repo.transition(
            adm,
            edge["platform_incident_id"],
            "acknowledged",
            "operator:alice",
            "looking",
            at(day + 40),
        )
        step(svc, [alert("EdgeModelInactive", day, day + 60)], day + 60)
        after = [i for i in repo.list_incidents(adm) if i["incident_key"] == "edge-runtime"][0]
        try:
            repo.transition(
                adm, edge["platform_incident_id"], "open", "operator:alice", "", at(day + 70)
            )
            illegal_refused = False
        except repo.IncidentError:
            illegal_refused = True
        ev.check(
            "a_status_a_person_set_survives_the_next_correlation_cycle_and_an_illegal_transition_is_refused",
            after["status"] == "acknowledged" and illegal_refused,
            after["status"],
        )
        step(
            svc,
            [alert("EdgeModelInactive", day, day + 60, active=False)],
            day + 60 + c.RESOLVE_AFTER_S + 5,
        )

        # ---- a new root merges an existing incident's signal
        day = 40_000
        step(svc, [alert("ApiErrorRateHigh", day, day + 30)], day + 30)
        (api,) = [i for i in repo.list_incidents(adm, "open") if i["incident_key"] == "api"]
        both = [
            alert("ApiErrorRateHigh", day, day + 60),
            alert(
                "PlatformTargetDown",
                day + 20,
                day + 60,
                severity="critical",
                service_name="postgres",
            ),
        ]
        step(svc, both, day + 60)
        merged_api = repo.list_incidents(adm, "resolved")
        api_after = next(
            i for i in merged_api if i["platform_incident_id"] == api["platform_incident_id"]
        )
        api_events = repo.timeline(adm, api["platform_incident_id"])
        pg = next(i for i in repo.list_incidents(adm, "open") if i["incident_key"] == "postgres")
        ev.check(
            "a_new_root_that_explains_an_open_incidents_signal_takes_it_over_and_the_old_incident_records_where_it_went",
            api_after["status"] == "resolved"
            and api_events[-1]["event"] == "resolved"
            and api_events[-1]["detail"].get("merged_into") == ["postgres"]
            and "api" in pg["components"]
            and pg["severity"] == "critical"
            and pg["hypothesis"]["suspected_root_component"] == "postgres",
            str(api_events[-1]["detail"]),
        )

        # ---- verified cause: a person, and only a person
        repo.record_verified_cause(
            adm,
            pg["platform_incident_id"],
            "disk full on the database host",
            "operator:alice",
            at(day + 100),
        )
        row = repo.list_incidents(adm, "open")
        row = next(i for i in row if i["incident_key"] == "postgres")
        step(svc, both, day + 120)
        row2 = next(i for i in repo.list_incidents(adm, "open") if i["incident_key"] == "postgres")
        ev.check(
            "a_person_can_record_a_verified_cause_and_the_machine_hypothesis_stays_labelled_unverified",
            row["verified_cause"] == "disk full on the database host"
            and row2["verified_cause"] == "disk full on the database host"
            and row2["hypothesis"]["verified"] is False
            and events_of(adm, pg["platform_incident_id"])[-1] == "cause_verified",
            str(events_of(adm, pg["platform_incident_id"])),
        )
        done = [
            alert(
                a.name,
                a.onset.timestamp() - SYNTHETIC.timestamp(),
                day + 60,
                active=False,
                **a.labels,
            )
            for a in both
        ]
        for a in done:
            a.severity = "high" if a.name == "PlatformTargetDown" else "medium"
        step(svc, done, day + 60 + c.RESOLVE_AFTER_S + 5)

        # ---- the history is a hash chain and nothing can rewrite it
        result = audit_chain.verify_table(adm, "platform_incident_events", "id", True)
        ev.check(
            "the_incident_history_is_a_hash_chain_that_verifies_intact",
            result["intact"] and result["rows"] > 10,
            f"{result['rows']} events, {len(result['hash_mismatches'])} mismatches",
        )
        try:
            adm.execute(
                "UPDATE platform_incident_events SET detail = '{}' WHERE id = (SELECT min(id) FROM platform_incident_events)"
            )
            rewritten = True
        except psycopg.Error:
            rewritten = False
        ev.check(
            "even_the_database_owner_cannot_rewrite_a_recorded_event",
            not rewritten
            and audit_chain.verify_table(adm, "platform_incident_events", "id", True)["intact"],
        )
        left = adm.execute(
            "SELECT count(*) FROM platform_incidents WHERE status <> 'resolved'"
        ).fetchone()[0]
        ev.metrics["lifecycle_scenarios"] = {
            "kind": "synthetic, hand-built signal sets",
            "incidents_created": adm.execute("SELECT count(*) FROM platform_incidents").fetchone()[
                0
            ],
            "events_recorded": result["rows"],
            "incidents_still_live_at_end": left,
        }


# -------------------------------------------------------------------------------------------------------- C. replay
def replay_checks() -> None:  # noqa: PLR0915
    if not ALERT_EVIDENCE.is_file():
        ev.check(
            "the_p10_04_lab_evidence_with_its_time_window_exists",
            False,
            "run verify_alerts.py first",
        )
        return
    window = json.loads(ALERT_EVIDENCE.read_text(encoding="utf-8"))["metrics"].get("lab_window_utc")
    ev.check("the_p10_04_lab_evidence_with_its_time_window_exists", bool(window), str(window))
    if not window:
        return
    parse = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)  # noqa: E731
    start, induced, cleared, finished = (
        parse(window[k]) for k in ("lab_started", "induced", "cleared", "finished")
    )
    lo, hi = (
        start - timedelta(minutes=2),
        finished + timedelta(seconds=c.RESOLVE_AFTER_S + 4 * STEP_S),
    )

    # The lab stops feeding Prometheus at `finished`; what happens to the platform afterwards (standing alerts because nothing runs,
    # traffic that vanishes) is not part of the fault timeline. Data ends there; time continues, and with no fresh data
    # a signal is no longer active, so incidents settle exactly as they would once every fault has cleared.
    series = platform_signals.fetch_alert_series(lo, finished + timedelta(seconds=STEP_S))
    ev.check(
        "prometheus_still_holds_the_alert_series_recorded_during_the_p10_04_lab_run",
        any(s["metric"].get("alertstate") == "firing" for s in series),
        f"{len(series)} ALERTS series in the window",
    )
    detector = platform_correlator.load_detector()
    ev.check("the_packaged_detector_from_p10_06_is_available", detector is not None)
    timeline = None
    if detector is not None:
        times, matrix = platform_signals.fetch_signal_matrix(lo, finished)
        timeline = platform_signals.build_anomaly_timeline(detector, times, matrix)
        ev.metrics["detector"] = {
            "served": detector.card.get("served_detector"),
            "threshold": detector.threshold,
            "signal_steps": len(times),
        }

    fresh_database(REPLAY_DB)
    with admin(REPLAY_DB) as adm, as_correlator(REPLAY_DB) as svc:
        now = lo
        syncs = 0
        while now <= hi:
            signals = platform_signals.alert_signals(series, now)
            if timeline is not None:
                signals += platform_signals.anomaly_signals(timeline, now)
            repo.sync(svc, c.correlate(signals, now), now)
            syncs += 1
            now += timedelta(seconds=STEP_S)

        incidents = repo.list_incidents(adm)
        rows = adm.execute(
            "SELECT i.platform_incident_id, i.incident_key, s.signal_key, s.source, s.active, s.occurrences "
            "FROM platform_incidents i JOIN platform_incident_signals s USING (platform_incident_id)"
        ).fetchall()
        # ---- what went in
        fired_alerts = sorted(
            {
                c.signal_key(
                    s["metric"]["alertname"],
                    {k: v for k, v in s["metric"].items() if k in c.SIGNAL_LABELS},
                )
                for s in series
                if s["metric"].get("alertstate") == "firing"
                and s["metric"]["alertname"] != c.WATCHDOG
            }
        )
        alert_rows = [r for r in rows if r[3] == "alert"]
        by_signal: dict[str, set] = {}
        for r in alert_rows:
            by_signal.setdefault(r[2], set()).add(r[1])
        lost = [k for k in fired_alerts if k not in by_signal]
        ev.check(
            "every_alert_that_fired_in_the_lab_run_lands_in_at_least_one_incident",
            not lost,
            f"{len(fired_alerts)} distinct alerts fired; lost: {lost}",
        )
        # final ownership: the incident a signal's ACTIVE row belongs to, or, when none is active, its last incident
        owners: dict[str, set] = {}
        for pid, key, signal, source, active, _ in rows:
            owners.setdefault(signal, set())
            if active:
                owners[signal].add(str(pid))
        double_active = {k: v for k, v in owners.items() if len(v) > 1}
        ev.check(
            "no_signal_is_active_in_two_incidents_at_once",
            not double_active,
            str(double_active),
        )
        unverified = adm.execute(
            "SELECT count(*) FROM platform_incidents WHERE truth_label <> 'inferred' OR NOT (hypothesis -> 'verified' = 'false'::jsonb) OR verified_cause IS NOT NULL"
        ).fetchone()[0]
        ev.check(
            "every_replayed_incident_is_labelled_inferred_with_an_unverified_hypothesis_and_no_cause_is_asserted_as_fact",
            incidents and unverified == 0,
            f"{len(incidents)} incidents, {unverified} not unverified",
        )
        live = [i for i in incidents if i["status"] != "resolved"]
        ev.check(
            "every_incident_is_resolved_after_the_faults_clear_and_stay_quiet",
            not live,
            f"still live: {[i['incident_key'] for i in live]}",
        )
        chain = audit_chain.verify_table(adm, "platform_incident_events", "id", True)
        ev.check(
            "the_replayed_incident_history_is_an_intact_hash_chain",
            chain["intact"],
            f"{chain['rows']} events",
        )

        # ---- what came out
        keys = sorted({i["incident_key"] for i in incidents})
        alert_keys = {r[2] for r in alert_rows}
        downstream_links = []
        for i in incidents:
            for link in i["hypothesis"].get("downstream", []):
                downstream_links.append(
                    {
                        "incident": i["incident_key"],
                        "symptom": link["signal"],
                        "explained_by": link["explained_by"],
                    }
                )
        seen_links = {(d["incident"], d["symptom"], d["explained_by"]) for d in downstream_links}
        anomaly_rows = [r for r in rows if r[3] == "anomaly"]
        anomaly_timing = {
            "before_the_faults": 0,
            "during_the_faults": 0,
            "after_the_faults_cleared": 0,
        }
        for signal, first_seen in adm.execute(
            "SELECT signal_key, first_seen FROM platform_incident_signals WHERE source = 'anomaly'"
        ).fetchall():
            if first_seen < induced:
                anomaly_timing["before_the_faults"] += 1
            elif first_seen <= cleared + timedelta(seconds=60):
                anomaly_timing["during_the_faults"] += 1
            else:
                anomaly_timing["after_the_faults_cleared"] += 1
        ev.metrics["anomaly_signals_by_first_seen"] = {
            **anomaly_timing,
            "note": "the detector was trained on the P10.05 synthetic load; the lab's healthy traffic differs from it, so an anomaly before "
            "the faults is a false alarm of the detector on data it was not trained for, reported as measured",
        }
        ev.metrics["replay"] = {
            "window_utc": window,
            "step_s": STEP_S,
            "syncs": syncs,
            "alerts_that_fired": len(fired_alerts),
            "alert_signals_in_incidents": len(alert_keys),
            "incidents_created": len(incidents),
            "distinct_incident_keys": keys,
            "reduction": f"{len(fired_alerts)} alerts -> {len(keys)} incident keys ({len(incidents)} incidents including reopened splits)",
            "signals_per_incident": {
                i["incident_key"] + "#" + str(n): i["signal_count"] for n, i in enumerate(incidents)
            },
            "causal_links_in_hypotheses": sorted(seen_links),
            "causal_links_the_injection_did_not_make": len(seen_links),
            "anomaly_signals": sorted({r[2] for r in anomaly_rows}),
            "alerts_by_incident_key": {k: sorted(v) for k, v in by_signal.items()},
        }
        gaps = []
        for i in incidents:
            evs = repo.timeline(adm, i["platform_incident_id"])
            first = next(e for e in evs if e["event"] == "opened")
            gaps.append((first["at"] - i["opened_at"]).total_seconds())
        ev.metrics["replay"]["seconds_from_first_firing_to_incident_opened"] = {
            "max": max(gaps),
            "median": sorted(gaps)[len(gaps) // 2],
            "note": "replay sync cadence is 15 s; the first firing sample is when the alert reached `firing`, after its `for:` delay",
        }
        ev.check(
            "fewer_incidents_than_alerts_so_duplicate_symptoms_are_grouped",
            len(keys) < len(fired_alerts),
            f"{len(fired_alerts)} alerts -> {len(keys)} incident keys",
        )
        ev.notes["lab_injection_independence"] = (
            "verify_alerts.py switches every fault on independently, at the same moment. Any causal link a hypothesis "
            "asserts between two lab faults is therefore an inference the lab cannot confirm - it is counted above, "
            "not defended."
        )


def main() -> int:
    world.load_platform_env()
    started = time.time()
    try:
        structure_checks()
        lifecycle_checks()
        replay_checks()
    finally:
        os.environ["POSTGRES_DB"] = "aiops"
        for name in (UNITS_DB, REPLAY_DB):
            try:
                world.use_database(name)
                world.drop_database(name)
            except psycopg.Error as exc:
                print(f"cleanup {name}: {exc}", flush=True)
        os.environ["POSTGRES_DB"] = "aiops"
    ev.metrics["seconds"] = round(time.time() - started)
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
