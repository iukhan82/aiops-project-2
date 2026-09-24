"""P06.07: candidate correlation on hand-built candidates over a small real-shaped
network. Grouping on real detector output, incident lifecycle against Postgres
and held-out incident metrics are proven by verify_incidents.py / P06.08."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.analytics.correlation import (
    CAUSE_KINDS,
    KIND_PRECEDENCE,
    Cand,
    Policy,
    Topology,
    correlate,
    group_confidence,
    group_severity,
    hypotheses,
    load_policy,
    view_at,
)
from backend.analytics.topology import Segment

T0 = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone.utc)
SEGS = [
    Segment("int-a1_int-a2", "int-a1", "int-a2", "corridor-a", "east", 1, 300.0, 15.0),
    Segment("int-a2_int-a3", "int-a2", "int-a3", "corridor-a", "east", 2, 300.0, 15.0),
    Segment("int-a3_int-a4", "int-a3", "int-a4", "corridor-a", "east", 3, 300.0, 15.0),
    Segment("int-a4_int-a3", "int-a4", "int-a3", "corridor-a", "west", 1, 300.0, 15.0),
    Segment("int-a1_int-b1", "int-a1", "int-b1", None, "cross", None, 300.0, 12.0),
    Segment("int-b1_int-b2", "int-b1", "int-b2", "corridor-b", "east", 1, 300.0, 15.0),
]
TOPO = Topology(SEGS)
POLICY = Policy(
    {
        "congestion": 0.85,
        "spillback": 0.5,
        "stalled_vehicle": {"single_source": 0.45, "corroborated": 0.62},
        "collision": 0.8,
        "wrong_way": 0.7,
        "flooding": 0.7,
        "low_visibility": 0.8,
        "pedestrian_conflict": 0.5,
    }
)
NOW = T0 + timedelta(hours=1)


def cand(
    kind,
    element,
    onset_s,
    end_s=None,
    *,
    etype="segment",
    severity="medium",
    modality="loop_detector",
    corroborated=False,
    cid=None,
) -> Cand:
    return Cand(
        cid or f"{kind}:{element}:{onset_s}",
        kind,
        etype,
        element,
        T0 + timedelta(seconds=onset_s),
        None if end_s is None else T0 + timedelta(seconds=end_s),
        T0 + timedelta(seconds=onset_s + 60),
        severity,
        0.9,
        "test",
        modality,
        corroborated,
        (f"ev-{kind}-{onset_s}",),
    )


def groups(*candidates):
    return correlate(list(candidates), TOPO, POLICY, NOW)


def test_repeated_candidates_from_one_detector_on_one_element_are_duplicates() -> None:
    (g,) = groups(
        cand("congestion", "int-a1_int-a2", 0, 300), cand("congestion", "int-a1_int-a2", 240, 600)
    )
    assert len(g.members) == 2 and set(g.relations.values()) == {"primary", "duplicate_source"}


def test_stall_and_the_queue_behind_it_are_one_incident_with_the_stall_as_cause() -> None:
    stall = cand("stalled_vehicle", "int-a2_int-a3", 0, 600, corroborated=True)
    queue = cand("congestion", "int-a1_int-a2", 120, 700)  # one segment upstream
    (g,) = groups(stall, queue)
    assert g.primary.kind == "stalled_vehicle" and g.relations[queue.candidate_id] == "consequence"


def test_congestion_downstream_of_a_blockage_is_not_its_consequence() -> None:
    result = groups(
        cand("stalled_vehicle", "int-a1_int-a2", 0, 600),
        cand("congestion", "int-a3_int-a4", 100, 700),
    )
    assert len(result) == 2


def test_queue_reaches_up_to_max_hops_upstream_across_a_junction() -> None:
    stall = cand("stalled_vehicle", "int-a2_int-a3", 0, 600)
    cross_street = cand(
        "congestion", "int-a1_int-b1", 200, 700
    )  # leaves int-a1 for int-b1: does not feed int-a2
    assert len(groups(stall, cross_street)) == 2
    feeder = cand("congestion", "int-a1_int-a2", 200, 700)  # feeds int-a2
    assert len(groups(stall, feeder)) == 1


def test_candidates_far_apart_in_time_are_separate_incidents() -> None:
    assert (
        len(
            groups(
                cand("congestion", "int-a1_int-a2", 0, 300),
                cand("congestion", "int-a1_int-a2", 1800, 2100),
            )
        )
        == 2
    )


def test_collision_and_stalled_vehicle_on_one_segment_are_two_views_of_one_event() -> None:
    (g,) = groups(
        cand("stalled_vehicle", "int-a2_int-a3", 0, 600),
        cand("collision", "int-a2_int-a3", 30, 600, modality="loop_detector"),
    )
    assert g.primary.kind == "collision" and set(g.relations.values()) == {
        "primary",
        "duplicate_source",
    }


def test_lone_pedestrian_conflict_is_its_own_group_and_links_only_to_a_nearby_collision() -> None:
    ped = cand("pedestrian_conflict", "int-a2", 0, 300, etype="intersection", modality="camera")
    assert len(groups(ped, cand("congestion", "int-a2_int-a3", 0, 300))) == 2
    (g,) = groups(ped, cand("collision", "int-a2_int-a3", 30, 300))
    assert g.relations[ped.candidate_id] == "related"


def test_corridor_hazard_groups_with_congestion_on_that_corridor_only() -> None:
    flood = cand(
        "flooding", "corridor-a", 0, 900, etype="corridor", modality="road_condition_sensor"
    )
    (g,) = groups(flood, cand("congestion", "int-a2_int-a3", 100, 800))
    assert g.primary.kind == "flooding"
    assert len(groups(flood, cand("congestion", "int-b1_int-b2", 100, 800))) == 2


def test_only_independent_sensor_modalities_add_confidence() -> None:
    loop_stall = cand("stalled_vehicle", "int-a2_int-a3", 0, 600, corroborated=True)
    loop_jam = cand("congestion", "int-a2_int-a3", 60, 600)
    (same_sensor,) = groups(loop_stall, loop_jam)
    assert group_confidence(same_sensor, POLICY) == pytest.approx(
        0.85
    )  # one modality: the best member, not a sum
    flood = cand(
        "flooding", "corridor-a", 0, 900, etype="corridor", modality="road_condition_sensor"
    )
    (two_sensors,) = groups(loop_stall, loop_jam, flood)
    assert group_confidence(two_sensors, POLICY) == pytest.approx(1 - 0.15 * 0.3, abs=1e-3)


def test_a_lone_low_precision_candidate_stays_below_the_opening_threshold() -> None:
    (g,) = groups(
        cand("pedestrian_conflict", "int-a2", 0, 300, etype="intersection", modality="camera")
    )
    assert group_confidence(g, POLICY) == 0.5 < 0.6
    (g,) = groups(cand("stalled_vehicle", "int-a2_int-a3", 0, 600, corroborated=False))
    assert group_confidence(g, POLICY) == 0.45


def test_severity_is_the_worst_member() -> None:
    (g,) = groups(
        cand("stalled_vehicle", "int-a2_int-a3", 0, 600, severity="medium"),
        cand("congestion", "int-a2_int-a3", 30, 600, severity="high"),
    )
    assert group_severity(g) == "high"


def test_hypotheses_are_ranked_labelled_and_never_a_verified_cause() -> None:
    (g,) = groups(
        cand("stalled_vehicle", "int-a2_int-a3", 0, 600, corroborated=True),
        cand("congestion", "int-a1_int-a2", 100, 700),
    )
    h = hypotheses(g, POLICY, TOPO)
    assert [x["rank"] for x in h] == list(range(1, len(h) + 1)) and all(
        "not verified" in x["hypothesis"] for x in h
    )
    assert (
        "int-a2_int-a3" in h[0]["hypothesis"] and "int-a1_int-a2" in h[0]["hypothesis"]
    )  # names the blockage and the queue
    (jam,) = groups(cand("congestion", "int-a1_int-a2", 0, 300))
    alt = hypotheses(jam, POLICY, TOPO)
    assert (
        len(alt) == 2
        and abs(sum(x["likelihood"] for x in alt) - 1.0) < 1e-3
        and "downstream" in alt[1]["hypothesis"]
    )


def test_a_flag_last_seen_long_ago_does_not_absorb_new_candidates() -> None:
    stale = Cand(
        "stale",
        "collision",
        "segment",
        "int-a2_int-a3",
        T0,
        None,
        T0 + timedelta(seconds=60),
        "high",
        0.9,
        "test",
        "loop_detector",
        False,
        ("e",),
        last_seen=T0 + timedelta(seconds=120),
    )
    later = cand("congestion", "int-a2_int-a3", 3000, 3500)
    assert len(correlate([stale, later], TOPO, POLICY, T0 + timedelta(seconds=4000))) == 2
    seen_now = Cand(
        "live",
        "collision",
        "segment",
        "int-a2_int-a3",
        T0,
        None,
        T0 + timedelta(seconds=60),
        "high",
        0.9,
        "test",
        "loop_detector",
        False,
        ("e",),
        last_seen=T0 + timedelta(seconds=3900),
    )
    assert len(correlate([seen_now, later], TOPO, POLICY, T0 + timedelta(seconds=4000))) == 1


def test_view_at_hides_the_future() -> None:
    c = cand("congestion", "int-a1_int-a2", 0, 900)  # detected at +60 s, clears at +900 s
    assert view_at(c, T0 + timedelta(seconds=30)) is None
    assert view_at(c, T0 + timedelta(seconds=300)).end is None
    assert view_at(c, T0 + timedelta(seconds=900)).end == c.end


def test_the_shipped_policy_covers_every_kind() -> None:
    policy = load_policy()
    for kind in KIND_PRECEDENCE:
        assert 0.0 < policy.precision(kind, True) <= 1.0
    assert set(CAUSE_KINDS) <= set(KIND_PRECEDENCE)
    assert policy.open_confidence >= 0.6
