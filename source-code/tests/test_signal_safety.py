"""P07.10: the independent runtime signal-safety monitor. No simulator: the
monitor is stdlib-only and works on raw state strings, so its rules are proven
directly - (a) on the real network's own programs, replayed second by second
(no false alarm on the unmodified design), and (b) on hand-built sequences
that break each rule exactly once (it does raise)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulator" / "control_adapters"))
from signal_safety import Design, SignalSafetyMonitor, TlDesign, load_design  # noqa: E402

NET = Path(__file__).resolve().parents[1] / "simulator" / "network" / "output" / "district.net.xml"


@pytest.fixture(scope="module")
def design() -> Design:
    return load_design(NET)


def replay(design: Design, tl_id: str, cycles: int = 3) -> SignalSafetyMonitor:
    """The program's own phases, each held for its lower-bound duration, one state per simulated second."""
    monitor = SignalSafetyMonitor(design)
    t = 0.0
    for _ in range(cycles):
        for state, duration, min_dur in design.tls[tl_id].phases:
            for _ in range(int(min_dur if min_dur is not None else duration)):
                monitor.observe(t, {tl_id: state})
                t += 1.0
    return monitor


def test_design_minimums_are_derived_from_the_networks_own_programs(design: Design) -> None:
    assert design.min_yellow_s == 3.0
    assert design.min_ped_green_s >= 5.0
    assert design.min_ped_clearance_s >= 0.0
    assert len(design.tls) == 12


def test_every_real_program_replays_with_zero_violations(design: Design) -> None:
    for tl_id in design.tls:
        assert replay(design, tl_id).violations == [], tl_id


# A tiny hand-built junction: links 0-1 vehicles (foes of each other), link 2 a pedestrian crossing (foe of both).
TOY = Design(
    {"toy": TlDesign("toy", 3, {2}, {0: {1, 2}, 1: {0, 2}, 2: {0, 1}}, [])},
    min_yellow_s=3.0,
    min_ped_green_s=5.0,
    min_ped_clearance_s=5.0,
)


def run(states: list[str]) -> SignalSafetyMonitor:
    monitor = SignalSafetyMonitor(TOY)
    for t, state in enumerate(states):
        monitor.observe(float(t), {"toy": state})
    return monitor


def kinds(monitor: SignalSafetyMonitor) -> set[str]:
    return {v.kind for v in monitor.violations}


def test_two_conflicting_protected_greens_are_flagged() -> None:
    assert "conflicting_green" in kinds(run(["Grr", "GGr"]))


def test_a_vehicle_green_against_a_crossing_green_is_flagged() -> None:
    assert "conflicting_green" in kinds(run(["rrG", "GrG"]))


def test_permissive_green_against_a_foe_is_the_networks_convention_not_a_violation() -> None:
    assert kinds(run(["Grr", "Ggr"])) == set()


def test_green_straight_to_red_is_flagged() -> None:
    assert "no_yellow_before_red" in kinds(run(["Grr", "Grr", "rrr"]))


def test_a_yellow_shorter_than_the_design_minimum_is_flagged() -> None:
    assert "short_yellow" in kinds(run(["Grr", "yrr", "yrr", "rrr"]))  # 2 s of yellow


def test_a_full_length_yellow_is_accepted() -> None:
    assert kinds(run(["Grr", "yrr", "yrr", "yrr", "rrr"])) == set()


def test_a_pedestrian_green_cut_short_is_flagged() -> None:
    assert "short_pedestrian_green" in kinds(run(["rrr", "rrG", "rrG", "rrr"]))


def test_a_vehicle_green_too_soon_after_a_crossing_is_flagged() -> None:
    assert "short_pedestrian_clearance" in kinds(
        run(["rrr"] + ["rrG"] * 5 + ["rrr", "rrr", "Grr"])
    )  # 2 s all-red < 5 s


def test_a_vehicle_green_after_the_full_clearance_is_accepted() -> None:
    assert kinds(run(["rrr"] + ["rrG"] * 5 + ["rrr"] * 5 + ["Grr"])) == set()


def test_nothing_before_the_first_observation_is_judged() -> None:
    monitor = run(["yrr", "rrr"])  # begins mid-yellow: its start was never seen
    assert monitor.violations == []


def test_summary_reports_counts_and_the_design_it_judged_against() -> None:
    summary = run(["Grr", "Grr", "rrr"]).summary()
    assert summary["violation_count"] == 1 and summary["violations_by_kind"] == {
        "no_yellow_before_red": 1
    }
    assert summary["design"]["min_yellow_s"] == 3.0
