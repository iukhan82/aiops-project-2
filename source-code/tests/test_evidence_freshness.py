"""SAFE-02 / SAFE-03: what counts as fresh evidence for a protected target (`backend/control/policy._evidence_fresh`).

The rule is a database query, so these tests use a stand-in connection that records the query and answers as the database would: what matters here is WHICH question is asked for each kind of target
and that "no row" means "not fresh". Against the real database the same rule is exercised by `acceptance/lab_stale_state.py` and `acceptance/lab_protected_actions.py`.
"""

from datetime import datetime, timezone

from backend.analytics.correlation import Topology
from backend.analytics.topology import Segment
from backend.control.policy import FRESH_EVIDENCE_MAX_AGE_S, _evidence_fresh

NOW = datetime(2026, 9, 26, 9, 0, 0, tzinfo=timezone.utc)
SEGMENTS = [
    Segment("int-a1_int-a2", "int-a1", "int-a2", "corridor-a", "east", 1, 300.0, 12.0),
    Segment("int-a1_int-b1", "int-a1", "int-b1", None, "cross", None, 300.0, 12.0),
]
TOPOLOGY = Topology(SEGMENTS, 3)


class Cursor:
    def __init__(self, conn) -> None:
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self.conn.queries.append((sql, params))

    def fetchone(self):
        return (1,) if self.conn.has_evidence else None


class Connection:
    def __init__(self, has_evidence: bool) -> None:
        self.has_evidence = has_evidence
        self.queries: list[tuple[str, tuple]] = []

    def cursor(self) -> Cursor:
        return Cursor(self)


def fresh(adapter: str, entity: str, has_evidence: bool) -> tuple[bool, list]:
    conn = Connection(has_evidence)
    return _evidence_fresh(conn, adapter, entity, "2026-09-18.1", TOPOLOGY, NOW), conn.queries


def test_a_cross_street_target_needs_recent_telemetry_from_the_junctions_at_its_ends():
    ok, queries = fresh("diversion_adapter", "int-a1_int-b1", has_evidence=True)
    assert ok is True
    (sql, params) = queries[0]
    assert "observation_events" in sql and "intersection_id" in sql
    assert params[0] == "int-a1" and params[1] == "int-b1"
    assert (NOW - params[2]).total_seconds() == FRESH_EVIDENCE_MAX_AGE_S


def test_a_cross_street_target_with_no_recent_telemetry_is_not_fresh_it_used_to_count_as_fresh_with_no_evidence_at_all():
    ok, queries = fresh("vms_adapter", "int-a1_int-b1", has_evidence=False)
    assert ok is False
    assert queries, "the database was asked"


def test_a_corridor_segment_is_judged_by_its_corridors_kpi_window_not_by_junction_telemetry():
    ok, queries = fresh("diversion_adapter", "int-a1_int-a2", has_evidence=True)
    assert ok is True and "corridor_kpis" in queries[0][0]
    assert fresh("diversion_adapter", "int-a1_int-a2", has_evidence=False)[0] is False


def test_a_target_that_is_not_on_the_network_is_never_fresh():
    ok, queries = fresh("diversion_adapter", "segment-that-does-not-exist", has_evidence=True)
    assert ok is False and not queries
