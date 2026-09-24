"""P09.03: the client every policy enforcement point uses to ask the policy decision point (Open Policy Agent).

Three questions go to OPA (policies in `source-code/policy/`):

* `api_decision`       - may these roles call this endpoint (`aiops/api/authz`)?
* `command_authority`  - may one of these roles request or review a command of this action type (`aiops/command`)?
* `command_decision`   - may this command be approved, or executed (`aiops/command`)?

The rule this module exists to keep: an engine that cannot answer is NOT an engine that said no, and it is never an engine that said
yes. Any failure to obtain a well-formed decision - connection refused, timeout, a non-200 answer, an undefined result, a result of the
wrong shape - raises `PolicyUnavailable`, and every caller treats that as "do not proceed" (the API answers 503, an approval leaves the
command requested and says the policy was unavailable, the executor does not execute). There is no local fallback that would let a
request through while the engine is down.
"""

from __future__ import annotations

import json
import os
import threading

import httpx

OPA_URL = os.environ.get("OPA_URL", "http://127.0.0.1:8181").rstrip("/")
TIMEOUT = httpx.Timeout(connect=1.0, read=2.0, write=2.0, pool=2.0)

API_PATH = "aiops/api/authz/decision"
AUTHORITY_PATH = "aiops/command/authority"
COMMAND_PATH = "aiops/command/decision"
COMMAND_DECISIONS = {"approved", "denied", "expired"}


class PolicyUnavailable(Exception):
    """The decision point could not produce a well-formed decision. Never means permit, and is not a denial either."""


_client: httpx.Client | None = None
_lock = threading.Lock()


def _http() -> httpx.Client:
    global _client  # noqa: PLW0603
    with _lock:
        if _client is None:
            _client = httpx.Client(
                timeout=TIMEOUT,
                trust_env=False,
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            )
        return _client


MAX_QUERY_URL = 7000


def _ask(path: str, payload: dict) -> httpx.Response:
    """The question is sent as `GET /v1/data/<path>?input=<json>` (OPA's "get a document with input"), falling back to POST only when the
    input is too large for a URL. Measured on the Windows development host, a POST to the engine in WSL takes about 44 ms whatever the
    engine does (the same POST takes 1.5 ms from inside WSL): the delay is in the Windows-to-WSL localhost relay and does not affect a
    GET. Both forms are the engine's own documented API and return the same decision."""
    url = f"{OPA_URL}/v1/data/{path}"
    encoded = json.dumps(payload, separators=(",", ":"))
    if len(url) + len(encoded) * 3 < MAX_QUERY_URL:
        return _http().get(url, params={"input": encoded})
    return _http().post(url, json={"input": payload})


def _post(path: str, payload: dict) -> httpx.Response:
    """One retry on a connection error only: a kept-alive connection to an engine that restarted fails on first use, and the question
    is a pure query, so asking again is safe. A timeout is not retried - a slow engine is answered as unavailable, promptly."""
    for attempt in (1, 2):
        try:
            return _ask(path, payload)
        except (
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
        ) as exc:
            if attempt == 2:
                raise PolicyUnavailable(
                    f"the policy engine could not be reached ({exc.__class__.__name__})"
                ) from exc
        except httpx.HTTPError as exc:
            raise PolicyUnavailable(
                f"the policy engine could not be reached ({exc.__class__.__name__})"
            ) from exc
    raise AssertionError("unreachable")


def query(path: str, payload: dict) -> dict:
    response = _post(path, payload)
    if response.status_code != 200:
        raise PolicyUnavailable(f"the policy engine answered HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise PolicyUnavailable("the policy engine answered something that is not JSON") from exc
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        raise PolicyUnavailable(
            "the policy engine returned no decision (the policy is undefined for this input)"
        )
    decision_id = body.get("decision_id")
    return {**result, "decision_id": decision_id} if isinstance(decision_id, str) else result


def api_decision(service: str, method: str, route_path: str, roles: list[str]) -> dict:
    result = query(
        API_PATH, {"service": service, "method": method, "path": route_path, "roles": roles}
    )
    if not isinstance(result.get("allow"), bool) or not isinstance(result.get("reason"), str):
        raise PolicyUnavailable("the policy engine returned a malformed API decision")
    return result


def command_authority(
    kind: str, action_type: str, critical_incident_on_target: bool, roles: list[str]
) -> dict:
    result = query(
        AUTHORITY_PATH,
        {
            "kind": kind,
            "action_type": action_type,
            "critical_incident_on_target": critical_incident_on_target,
            "roles": roles,
        },
    )
    if not isinstance(result.get("allow"), bool) or not isinstance(result.get("reason"), str):
        raise PolicyUnavailable("the policy engine returned a malformed command-authority decision")
    if result["allow"] and not (
        isinstance(result.get("role"), str) and isinstance(result.get("safety_class"), str)
    ):
        raise PolicyUnavailable(
            "the policy engine permitted a command without naming the role and class"
        )
    return result


def command_decision(facts: dict) -> dict:
    result = query(COMMAND_PATH, facts)
    if result.get("decision") not in COMMAND_DECISIONS:
        raise PolicyUnavailable("the policy engine returned a malformed command decision")
    return result


def loaded_version() -> str | None:
    """The version of the policy bundle the engine is serving, or None when it does not answer or has not loaded it. Used for platform
    status and the deployment check only - never as a shortcut that lets a decision skip the engine."""
    try:
        response = _http().get(f"{OPA_URL}/v1/data/aiops/model/version")
        result = response.json().get("result") if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return result if isinstance(result, str) else None
