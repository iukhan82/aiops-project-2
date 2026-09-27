"""P10.08: the remediation registry's own invariants - what makes every registered action bounded."""

import pytest

from backend.aiops import correlation, remediation as r
from backend.aiops.remediation_adapters import (
    DockerAdapter,
    NotExecutable,
    PlanOnlyAdapter,
    TraceSamplingAdapter,
    allowed_targets,
)
from backend.roles import HUMAN_ROLES, SAFETY_CLASS_OF_ACTION

STATEFUL = {"aiops-postgres", "aiops-kafka-broker", "aiops-keycloak", "aiops-mqtt-broker"}


def test_the_six_required_kinds_are_each_registered():
    assert {a.kind for a in r.ACTIONS.values()} == set(r.KINDS)


def test_every_action_is_bounded_on_every_axis():
    for a in r.ACTIONS.values():
        assert a.kind in r.KINDS and a.adapter in {
            "docker",
            "model_registry",
            "trace_sampling",
            "plan_only",
        }, a.id
        assert a.targets, f"{a.id} names no target"
        assert 1 <= a.max_attempts_per_incident <= 2, a.id
        assert 60 <= a.cooldown_s <= 3600, a.id
        assert 1 <= a.max_per_window <= 5 and a.window_s >= a.cooldown_s, a.id
        assert 0 < a.execute_timeout_s <= 300, a.id
        assert 0 < a.verify_sustain_s <= 600 and a.verify_sustain_s < a.verify_timeout_s <= 1800, (
            a.id
        )
        for t in a.targets:
            assert t.autonomy in r.AUTONOMY, (a.id, t.id)
        for name, (low, high) in a.params.items():
            assert low < high, (a.id, name)
    assert r.MAX_IN_FLIGHT == 1
    assert r.MAX_ATTEMPTS_PER_INCIDENT >= max(
        a.max_attempts_per_incident for a in r.ACTIONS.values()
    )


def test_a_plan_only_adapter_never_carries_an_auto_target():
    for a in r.ACTIONS.values():
        if a.adapter == "plan_only":
            assert all(t.autonomy == "plan_only" for t in a.targets), a.id
        else:
            assert all(t.autonomy != "plan_only" for t in a.targets), a.id


def test_failover_scale_and_quarantine_are_plan_only_on_this_single_host():
    for kind in ("failover", "scale", "quarantine"):
        assert {a.adapter for a in r.ACTIONS.values() if a.kind == kind} == {"plan_only"}


def test_a_stateful_service_is_never_restarted_without_a_person():
    restart = r.ACTIONS["restart_container"]
    for t in restart.targets:
        if t.id in STATEFUL:
            assert t.autonomy == "approval", t.id
    assert {t.id for t in restart.targets if t.autonomy == "auto"} == {"aiops-edge-runtime"}


def test_the_policy_engine_is_not_a_remediation_target_because_it_cannot_be_repaired_by_something_that_needs_it():
    for a in r.ACTIONS.values():
        assert all("opa" not in t.id for t in a.targets), a.id
    assert all(rule.component != "opa" for rule in r.PLAYBOOK)


def test_no_registered_action_is_a_traffic_or_emergency_action():
    assert set(r.ACTIONS).isdisjoint(SAFETY_CLASS_OF_ACTION)
    for a in r.ACTIONS.values():
        assert "signal" not in a.id and "preempt" not in a.id and "diversion" not in a.id


def test_the_remediation_identity_is_a_service_identity_not_a_human_role():
    assert r.PLATFORM_ACTOR.startswith("system:")
    assert r.PLATFORM_ACTOR not in HUMAN_ROLES


def test_every_playbook_line_names_a_registered_action_a_listed_target_and_in_bounds_parameters():
    for rule in r.PLAYBOOK:
        spec = r.ACTIONS[rule.action]
        assert spec.target(rule.target) is not None, (rule.action, rule.target)
        assert set(rule.params) == set(spec.params), (rule.action, rule.params)
        for name, value in rule.params.items():
            low, high = spec.params[name]
            assert low <= value <= high, (rule.action, name)
        assert rule.component in correlation.COMPONENTS
        assert rule.signals and rule.clears
        for alert in rule.signals | rule.clears:
            assert alert in correlation.ALERT_MODEL, alert
        assert rule.why


def test_a_playbook_line_only_acts_on_signals_its_component_can_actually_raise():
    for rule in r.PLAYBOOK:
        for alert in rule.signals:
            model = correlation.ALERT_MODEL[alert]
            if model.component != "service_name":
                assert model.component == rule.component, (rule.action, alert)


def test_candidates_come_from_the_playbook_in_order_and_only_for_active_matching_signals():
    active = [
        {"signal": "EdgeModelErrors", "signal_key": "EdgeModelErrors{code=integrity_mismatch}"}
    ]
    cands = r.candidates("edge-runtime", active)
    assert [c.rule.action for c in cands] == ["rollback_model", "restart_container"]
    assert cands[0].motivating == ("EdgeModelErrors{code=integrity_mismatch}",)
    assert r.candidates("edge-runtime", []) == []
    assert r.candidates("api", active) == []


def test_the_adapters_only_accept_the_targets_the_registry_lists_for_them():
    assert allowed_targets("docker") == {t.id for t in r.ACTIONS["restart_container"].targets}
    outcome = DockerAdapter(run=lambda *a, **k: pytest.fail("must not touch docker")).execute(
        "some-other-container", {}
    )
    assert not outcome.ok and "not registered" in outcome.detail["error"]


def test_a_plan_only_adapter_refuses_to_execute_and_describes_what_a_person_would_do():
    adapter = PlanOnlyAdapter()
    with pytest.raises(NotExecutable):
        adapter.execute("api", {"replicas": 2})
    assert "nothing is executed" in adapter.plan("api", {"replicas": 2})["do"]


def test_the_sampling_adapter_refuses_out_of_bounds_parameters_and_undo_restores(tmp_path):
    path = tmp_path / "s.json"
    adapter = TraceSamplingAdapter(path)
    assert not adapter.execute("otel-traces", {"ratio": 0.001, "ttl_s": 600}).ok
    assert not adapter.execute("otel-traces", {"ratio": 0.1, "ttl_s": 999999}).ok
    assert not adapter.execute("not-registered", {"ratio": 0.1, "ttl_s": 600}).ok
    assert not path.exists()
    assert adapter.execute("otel-traces", {"ratio": 0.1, "ttl_s": 600}).ok
    assert path.exists()
    adapter.undo("otel-traces", {})
    assert not path.exists()
