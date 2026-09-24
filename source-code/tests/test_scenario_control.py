"""P05.09 / P08.09: scenario-control pure-config unit tests. Real-Keycloak, real-database and real-server behaviour (refusal of a missing,
forged or tampered identity, the person named in the audit trail, deterministic replay, the concurrent-run bound under genuinely
concurrent requests, no writes to operational tables) needs the real stack under uvicorn and is proven by
source-code/backend/scenario_control/verify_scenario_control.py - see docs/evidence/p08_09_demo_controls_evidence.json.
"""

from backend.api.authz import capabilities_of, inventory, policy_for
from backend.scenario_control.app import ACTION_OF, MAX_CONCURRENT_RUNS, SERVICE


def test_only_the_demo_operator_holds_demo_control() -> None:
    assert inventory()["capabilities"]["demo.control"]["roles"] == ["demo_operator"]
    assert "demo.control" in capabilities_of(("demo_operator",))
    for role in (
        "operator",
        "supervisor",
        "dispatcher",
        "incident_commander",
        "field_responder",
        "auditor",
    ):
        assert "demo.control" not in capabilities_of((role,))


def test_every_scenario_control_endpoint_is_listed_and_needs_demo_control_except_the_health_probe() -> (
    None
):
    listed = [a for a in inventory()["apis"] if a.get("service") == SERVICE]
    assert listed
    for api in listed:
        policy = policy_for(api["method"], api["path"], SERVICE)
        assert policy is not None
        if api.get("public"):
            assert api["path"].endswith("/health")
        else:
            assert policy.capability == "demo.control"


def test_every_audited_action_maps_to_a_listed_endpoint_and_none_is_left_unmapped() -> None:
    protected = {
        (a["method"], a["path"])
        for a in inventory()["apis"]
        if a.get("service") == SERVICE and not a.get("public")
    }
    assert set(ACTION_OF) == protected
    assert set(ACTION_OF.values()) == {"start", "list", "status", "reset", "replay", "audit"}


def test_the_operator_api_does_not_serve_the_demo_endpoints_and_the_reverse() -> None:
    assert policy_for("POST", "/scenario-control/v1/runs") is None
    assert policy_for("GET", "/api/v1/devices", SERVICE) is None


def test_concurrent_run_bound_is_positive_and_small() -> None:
    assert 0 < MAX_CONCURRENT_RUNS <= 10
