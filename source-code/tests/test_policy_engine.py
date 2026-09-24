"""P09.03: the parts of the policy-engine integration that do not need a running engine.

What needs the engine - the Rego itself, the differential test against the Python reference, the outage behaviour of the real API and
the executor - is proven by `policy/verify_policy.py` (docs/evidence/p09_03_policy.json) against real OPA, Keycloak and PostgreSQL.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from backend import pdp
from backend.api.hardening import BodyLimit, RateLimiter, SecurityHeaders
from backend.control.policy import PolicyContext, context_to_input, evaluate
from backend.roles import EXECUTOR

SOURCE_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone.utc)


def load_build_data():
    spec = importlib.util.spec_from_file_location(
        "policy_build_data", SOURCE_ROOT / "policy" / "build_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine():
    """Point the client at a scripted fake engine: `engine(handler)` installs `handler(request) -> httpx.Response`."""
    original = pdp._client

    def install(handler) -> None:
        pdp._client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)

    yield install
    pdp._client = original


# ------------------------------------------------------------------ the client fails closed


def test_a_well_formed_api_decision_is_returned(engine) -> None:
    engine(
        lambda r: httpx.Response(
            200, json={"result": {"allow": True, "reason": "permit", "capability": "map.view"}}
        )
    )
    assert pdp.api_decision("api", "GET", "/api/v1/map", ["operator"])["allow"] is True


def test_the_question_names_the_route_template_and_the_roles(engine) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["input"] = json.loads(request.url.params["input"])
        return httpx.Response(
            200, json={"decision_id": "d-1", "result": {"allow": False, "reason": "forbidden"}}
        )

    engine(handler)
    answer = pdp.api_decision("api", "POST", "/api/v1/commands", ["auditor"])
    assert seen["method"] == "GET" and seen["path"] == "/v1/data/aiops/api/authz/decision"
    assert seen["input"] == {
        "service": "api",
        "method": "POST",
        "path": "/api/v1/commands",
        "roles": ["auditor"],
    }
    assert answer["decision_id"] == "d-1"


def test_an_input_too_large_for_a_url_is_posted_instead(engine) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return httpx.Response(200, json={"result": {"allow": False, "reason": "forbidden"}})

    engine(handler)
    pdp.api_decision("api", "GET", "/api/v1/x", ["r" * 5000])
    assert seen["method"] == "POST"


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")),
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
        lambda r: httpx.Response(500, text="boom"),
        lambda r: httpx.Response(200, text="<html>not json</html>"),
        lambda r: httpx.Response(200, json={}),  # the policy is undefined for this input
        lambda r: httpx.Response(200, json={"result": "allow"}),
        lambda r: httpx.Response(200, json={"result": {"allow": "yes", "reason": "permit"}}),
        lambda r: httpx.Response(200, json={"result": {"allow": True}}),
    ],
)
def test_anything_but_a_well_formed_decision_is_policy_unavailable_never_a_permit(
    engine, handler
) -> None:
    engine(handler)
    with pytest.raises(pdp.PolicyUnavailable):
        pdp.api_decision("api", "GET", "/api/v1/map", ["operator"])


def test_a_permitted_authority_must_name_the_role_and_class(engine) -> None:
    engine(
        lambda r: httpx.Response(
            200, json={"result": {"allow": True, "reason": "permit", "role": None}}
        )
    )
    with pytest.raises(pdp.PolicyUnavailable):
        pdp.command_authority("request", "diversion", False, ["operator"])


def test_a_command_decision_outside_the_three_outcomes_is_unavailable(engine) -> None:
    engine(lambda r: httpx.Response(200, json={"result": {"decision": "maybe"}}))
    with pytest.raises(pdp.PolicyUnavailable):
        pdp.command_decision({})


def test_the_loaded_version_is_none_when_the_engine_does_not_answer(engine) -> None:
    engine(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    assert pdp.loaded_version() is None
    engine(lambda r: httpx.Response(200, json={}))
    assert pdp.loaded_version() is None
    engine(lambda r: httpx.Response(200, json={"result": "abc123"}))
    assert pdp.loaded_version() == "abc123"


# ------------------------------------------------------------------ the policy data cannot drift from its sources


def test_the_generated_policy_data_is_current() -> None:
    module = load_build_data()
    assert module.DATA_PATH.read_text(encoding="utf-8").replace("\r\n", "\n") == module.render(
        module.build()
    ), "policy data is out of date: run python source-code/policy/build_data.py"


def test_the_policy_data_carries_every_endpoint_and_capability_of_the_inventory() -> None:
    module = load_build_data()
    data = json.loads(module.DATA_PATH.read_text(encoding="utf-8"))
    inv = json.loads(
        (SOURCE_ROOT / "frontend" / "src" / "config" / "inventory.json").read_text(encoding="utf-8")
    )
    assert len(data["endpoints"]) == len(inv["apis"])
    assert set(data["capabilities"]) == set(inv["capabilities"])
    assert data["endpoints"]["api GET /api/v1/health"] == {"capability": None, "public": True}


def test_the_policy_version_changes_when_a_policy_file_changes(tmp_path, monkeypatch) -> None:
    module = load_build_data()
    before = module.build()["version"]
    copy = tmp_path / "policy"
    copy.mkdir()
    for path in module.rego_files():
        target = copy / path.relative_to(module.POLICY_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    monkeypatch.setattr(module, "POLICY_DIR", copy)
    assert module.build()["version"] != before


# ------------------------------------------------------------------ the facts sent to the engine


def ctx(**overrides) -> PolicyContext:
    base = dict(
        action_type="diversion",
        target_adapter="diversion_adapter",
        target_entity_id="seg-1",
        requested_by="operator:alice",
        expires_at=NOW + timedelta(minutes=5),
        approver="supervisor:bob",
        approver_role="supervisor",
        now=NOW,
        target_exists=True,
        target_evidence_fresh=True,
        recommendation_id=None,
        recommendation_action_type=None,
        recommendation_status=None,
        safety_class="SC-1",
        requester_role="operator",
    )
    base.update(overrides)
    return PolicyContext(**base)


def test_the_input_sent_to_the_engine_does_not_carry_a_safety_class() -> None:
    facts = context_to_input(ctx())
    assert "safety_class" not in facts
    assert facts["critical_incident_on_target"] is False
    assert facts["phase"] == "approval"
    assert facts["expires_at"] == (NOW + timedelta(minutes=5)).isoformat()


def test_a_context_whose_class_disagrees_with_its_facts_is_refused() -> None:
    with pytest.raises(ValueError):
        context_to_input(ctx(safety_class="SC-0"))  # a diversion is SC-1


def test_a_critical_incident_flag_agrees_with_an_sc2_class() -> None:
    facts = context_to_input(ctx(safety_class="SC-2", critical_incident_on_target=True))
    assert facts["critical_incident_on_target"] is True


def test_execution_facts_carry_the_actor_and_the_command_status_and_no_recommendation() -> None:
    facts = context_to_input(
        ctx(phase="execution", actor=EXECUTOR, command_status="approved", recommendation_id=None)
    )
    assert facts["actor"] == EXECUTOR and facts["command_status"] == "approved"
    assert facts["recommendation"] is None


# ------------------------------------------------------------------ the Python reference for the execution phase


def execution(**overrides) -> PolicyContext:
    facts = {"phase": "execution", "actor": EXECUTOR, "command_status": "approved", **overrides}
    return ctx(**facts)


def test_a_legitimately_approved_command_passes_the_execution_reference() -> None:
    assert evaluate(execution()).decision == "approved"


def test_only_the_executor_identity_may_execute() -> None:
    result = evaluate(execution(actor="supervisor:bob"))
    assert result.decision == "denied" and "EXECUTE authority" in result.message


def test_a_command_that_is_not_approved_is_never_executed() -> None:
    for status in ("requested", "executing", "executed", "denied", "expired"):
        assert evaluate(execution(command_status=status)).decision == "denied", status


def test_a_command_with_no_recorded_approver_is_never_executed() -> None:
    result = evaluate(execution(approver=None, approver_role=None))
    assert result.decision == "denied" and "no approval is recorded" in result.message


def test_execution_rechecks_that_the_approver_was_not_the_requester() -> None:
    result = evaluate(execution(approver="operator:alice"))
    assert result.decision == "denied" and "four-eyes" in result.message


def test_execution_rechecks_evidence_freshness_and_expiry() -> None:
    assert evaluate(execution(target_evidence_fresh=False)).error_code == "stale_evidence"
    assert evaluate(execution(now=NOW + timedelta(minutes=6))).decision == "expired"


def test_execution_ignores_the_recommendation_state() -> None:
    result = evaluate(
        execution(
            recommendation_id="r1",
            recommendation_action_type="diversion",
            recommendation_status="superseded",
        )
    )
    assert result.decision == "approved"


# ------------------------------------------------------------------ rate limiting


def test_a_bucket_allows_its_burst_then_refuses_and_says_how_long_to_wait() -> None:
    now = [0.0]
    limiter = RateLimiter(rate_per_s=2.0, burst=3.0, clock=lambda: now[0])
    assert [limiter.check("a") for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = limiter.check("a")
    assert wait == pytest.approx(0.5)
    now[0] += 0.5
    assert limiter.check("a") == 0.0


def test_buckets_are_independent_per_key() -> None:
    limiter = RateLimiter(rate_per_s=1.0, burst=1.0, clock=lambda: 0.0)
    assert limiter.check("a") == 0.0 and limiter.check("a") > 0.0
    assert limiter.check("b") == 0.0


def test_a_bucket_never_refills_beyond_its_burst() -> None:
    now = [0.0]
    limiter = RateLimiter(rate_per_s=10.0, burst=2.0, clock=lambda: now[0])
    limiter.check("a")
    now[0] += 3600.0
    assert [limiter.check("a") for _ in range(2)] == [0.0, 0.0]
    assert limiter.check("a") > 0.0


def test_a_refused_check_does_not_spend_tokens() -> None:
    now = [0.0]
    limiter = RateLimiter(rate_per_s=1.0, burst=1.0, clock=lambda: now[0])
    limiter.check("a")
    for _ in range(50):
        limiter.check("a")
    now[0] += 1.0
    assert limiter.check("a") == 0.0


# ------------------------------------------------------------------ headers and body size


async def echo(request):
    return JSONResponse({"length": len(await request.body())})


async def plain(request):
    return PlainTextResponse("ok")


def hardened() -> TestClient:
    app = Starlette(routes=[Route("/echo", echo, methods=["POST"]), Route("/plain", plain)])
    app.add_middleware(BodyLimit, max_bytes=1000)
    app.add_middleware(SecurityHeaders)
    return TestClient(app)


def test_every_response_carries_the_security_headers_including_errors() -> None:
    client = hardened()
    for response in (client.get("/plain"), client.get("/missing"), client.post("/plain")):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["cache-control"] == "no-store"
        assert "default-src 'none'" in response.headers["content-security-policy"]


def test_no_cross_origin_header_is_ever_sent() -> None:
    response = hardened().get("/plain", headers={"Origin": "https://elsewhere.example"})
    assert not any(name.lower().startswith("access-control-") for name in response.headers)


def test_a_body_within_the_limit_is_read() -> None:
    assert hardened().post("/echo", content=b"x" * 1000).json() == {"length": 1000}


def test_a_declared_oversize_body_is_refused_with_413() -> None:
    response = hardened().post("/echo", content=b"x" * 1001)
    assert response.status_code == 413
    assert response.json()["detail"]["error"] == "body_too_large"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_streamed_oversize_body_is_refused_with_413() -> None:
    def chunks():
        for _ in range(20):
            yield b"x" * 100

    response = hardened().post("/echo", content=chunks())
    assert response.status_code == 413


def test_a_malformed_content_length_is_refused() -> None:
    response = hardened().post("/echo", content=b"x", headers={"Content-Length": "abc"})
    assert response.status_code in (400, 413)
