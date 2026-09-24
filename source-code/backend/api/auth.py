"""P08.04 / P09.02: OpenID Connect token validation and per-endpoint capability
enforcement for the operator API.

Every request carries a Keycloak-issued access token (`Authorization: Bearer`;
for the WebSocket, `?access_token=` because a browser cannot set headers on a
WebSocket). It is validated strictly:

* signature against the realm's published keys (JWKS, cached), and only the
  algorithm we expect (RS256) is accepted - `alg=none` and HS256-with-the-public-key
  confusion are refused by construction;
* issuer must equal the configured realm URL, audience must contain `aiops-api`,
  `exp`/`iat`/`sub` are required, a small clock leeway is allowed;
* the authorized party (`azp`) must be a client we issued the token for;
* the token must carry at least one operating role we know (`backend/roles.py`).

Then the policy decision point decides (P09.03): the endpoint's required
capability (from the UX inventory, generated into the policy engine's data by
`policy/build_data.py`) must be held by the principal's roles. 401 when
unauthenticated, 403 when authenticated but not permitted, 503 when the
identity provider or the policy engine cannot be reached (fail closed, never
open - there is no in-process fallback that would let a request through while
the engine is down).

`AIOPS_AUTH_MODE=off` exists only so the data-API verification scripts, which
test data rather than authentication, can call the app without a token. It logs
a loud warning, `/api/v1/health` reports the mode, and the browser stack and
`verify_auth.py` assert it is `oidc`.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass

import jwt
import psycopg
from fastapi import HTTPException, WebSocketException
from jwt import PyJWKClient
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection

from backend import pdp
from backend.api.authz import capabilities_of, policy_for
from backend.api.hardening import FAILURES, client_address, limit_person
from backend.roles import HUMAN_ROLES, SERVICE_IDENTITIES
from database.migrate import dsn_from_env

log = logging.getLogger("aiops.auth")

ISSUER = os.environ.get("KEYCLOAK_ISSUER", "http://localhost:8180/realms/aiops")
JWKS_URL = os.environ.get(
    "KEYCLOAK_JWKS_URL", "http://127.0.0.1:8180/realms/aiops/protocol/openid-connect/certs"
)
AUDIENCE = os.environ.get("KEYCLOAK_AUDIENCE", "aiops-api")
ALLOWED_CLIENTS = frozenset(
    {
        "aiops-ui",
        "system-command-executor",
        "system-outcome-verifier",
        "system-optimization-engine",
        "system-emergency-engine",
        "system-aiops-remediation",
    }
)
KNOWN_ROLES = frozenset(HUMAN_ROLES) | frozenset(SERVICE_IDENTITIES)
LEEWAY_S = 5


def auth_mode() -> str:
    return "off" if os.environ.get("AIOPS_AUTH_MODE", "oidc").lower() == "off" else "oidc"


if auth_mode() == "off":
    log.warning(
        "AIOPS_AUTH_MODE=off: the API is NOT authenticating requests. Never use this outside the data-API verification scripts."
    )


@dataclass(frozen=True)
class Principal:
    sub: str
    username: str
    name: str
    roles: tuple[str, ...]
    capabilities: frozenset[str]
    expires_at: int
    client: str


class Denied(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: object):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


_jwks: PyJWKClient | None = None


def _jwks_client() -> PyJWKClient:
    global _jwks  # noqa: PLW0603
    if _jwks is None:
        _jwks = PyJWKClient(JWKS_URL, cache_keys=True, max_cached_keys=16, lifespan=600, timeout=5)
    return _jwks


def verify_token(token: str) -> dict:
    """Blocking (may fetch the JWKS once). Raises Denied."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise Denied(401, "invalid_token", "the token is not a well-formed JWT") from exc
    if header.get("alg") != "RS256":
        raise Denied(401, "invalid_token", f"unexpected signing algorithm {header.get('alg')!r}")
    try:
        key = _jwks_client().get_signing_key_from_jwt(token).key
    except jwt.PyJWKClientConnectionError as exc:
        raise Denied(
            503,
            "identity_provider_unavailable",
            "the identity provider could not be reached; refusing rather than guessing",
        ) from exc
    except jwt.PyJWTError as exc:
        raise Denied(401, "invalid_token", "no matching signing key") from exc
    try:
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            leeway=LEEWAY_S,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Denied(401, "token_expired", "the token has expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise Denied(401, "invalid_audience", f"the token was not issued for {AUDIENCE!r}") from exc
    except jwt.InvalidIssuerError as exc:
        raise Denied(401, "invalid_issuer", "the token was issued by a different realm") from exc
    except jwt.PyJWTError as exc:
        raise Denied(401, "invalid_token", str(exc)) from exc


def principal_from_claims(claims: dict) -> Principal:
    client = claims.get("azp", "")
    if client not in ALLOWED_CLIENTS:
        raise Denied(401, "invalid_client", f"the token was issued to an unknown client {client!r}")
    roles = tuple(
        sorted(r for r in claims.get("realm_access", {}).get("roles", []) if r in KNOWN_ROLES)
    )
    if not roles:
        raise Denied(
            403, "no_operating_role", "the token carries no operating role", capability=None
        )
    return Principal(
        sub=claims["sub"],
        username=claims.get("preferred_username", claims["sub"]),
        name=claims.get("name", claims.get("preferred_username", "")),
        roles=roles,
        capabilities=capabilities_of(roles),
        expires_at=int(claims["exp"]),
        client=client,
    )


def _token_from(conn: HTTPConnection) -> str | None:
    header = conn.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return conn.query_params.get("access_token")


def _off_principal() -> Principal:
    roles = tuple(HUMAN_ROLES)
    return Principal(
        "auth-off",
        "auth-off",
        "Authentication disabled",
        roles,
        capabilities_of(roles),
        2**31 - 1,
        "auth-off",
    )


def _raise(conn: HTTPConnection, denied: Denied) -> None:
    if conn.scope["type"] == "websocket":
        raise WebSocketException(code=1008, reason=denied.code)
    headers = (
        {"WWW-Authenticate": f'Bearer error="{denied.code}"'} if denied.status == 401 else None
    )
    if denied.status == 429:
        headers = {"Retry-After": str(denied.extra.get("retry_after_s", 1))}
    raise HTTPException(
        denied.status,
        detail={"error": denied.code, "message": denied.message, **denied.extra},
        headers=headers,
    )


def _record_denial(
    username: str,
    roles: list[str],
    method: str,
    path: str,
    capability: str | None,
    reason: str = "missing capability",
) -> None:
    """A signed-in person asked for something their role does not allow: that belongs in the audit trail. Best effort - a database that
    cannot be reached must not turn a refusal into a different error."""
    try:
        with psycopg.connect(dsn_from_env(), connect_timeout=2) as db, db.cursor() as cur:
            cur.execute(
                "INSERT INTO operator_audit (actor, actor_roles, action, entity_type, entity_id, outcome, detail) VALUES (%s, %s, %s, %s, %s, 'denied', %s::jsonb)",
                (
                    username,
                    roles,
                    f"{method} {path}",
                    "endpoint",
                    None,
                    json.dumps({"reason": reason, "capability": capability}),
                ),
            )
    except Exception:  # noqa: BLE001
        log.warning("could not record an access denial for %s", username)


OUTAGE_AUDIT_EVERY_S = 30.0
_last_outage_audit = -OUTAGE_AUDIT_EVERY_S


def _worth_auditing(denied: Denied) -> bool:
    """A refusal for a missing capability is always audited. During a policy outage every request fails, so one audit row per interval
    says so without writing one per request."""
    global _last_outage_audit  # noqa: PLW0603
    if denied.code == "forbidden":
        return True
    if denied.code != "policy_unavailable":
        return False
    if time.monotonic() - _last_outage_audit < OUTAGE_AUDIT_EVERY_S:
        return False
    _last_outage_audit = time.monotonic()
    return True


def refuse_unidentified(address: str, denied: Denied) -> Denied:
    """A request that could not be identified spends from its address's failure allowance. Callers who ARE identified are never
    affected, so one workstation guessing tokens cannot lock out the people sharing its address; once the allowance is spent, the
    refusal becomes 429 so the guessing is throttled rather than answered."""
    wait = FAILURES.check(address)
    if wait <= 0:
        return denied
    return Denied(
        429,
        "rate_limited",
        "Too many requests without a valid identity came from this address. Wait before trying again.",
        retry_after_s=max(1, math.ceil(wait)),
    )


async def authorize(principal: Principal, service: str, method: str, route_path: str) -> None:
    """Ask the policy decision point whether this principal may call this endpoint. Raises Denied: 403 with the engine's reason when it
    says no, 429 when this person is sending faster than the service accepts, 503 when the engine cannot be asked or its answer is
    malformed."""
    wait = limit_person(principal.sub, method)
    if wait > 0:
        raise Denied(
            429,
            "rate_limited",
            "You are sending requests faster than this service accepts. Wait a moment and try again.",
            retry_after_s=max(1, math.ceil(wait)),
        )
    try:
        decision = await run_in_threadpool(
            pdp.api_decision, service, method, route_path, list(principal.roles)
        )
    except pdp.PolicyUnavailable as exc:
        raise Denied(
            503,
            "policy_unavailable",
            "the policy engine could not be reached; refusing rather than guessing",
        ) from exc
    if decision["allow"]:
        return
    reason = decision["reason"]
    capability = decision.get("capability")
    if reason == "no_operating_role":
        raise Denied(
            403, "no_operating_role", "the token carries no operating role", capability=None
        )
    if reason == "unlisted_endpoint":
        raise Denied(
            403,
            "unlisted_endpoint",
            "this endpoint is not in the access inventory, so it is denied by default",
        )
    raise Denied(
        403,
        "forbidden",
        f"your role does not hold the {capability!r} capability",
        capability=capability,
        roles=list(principal.roles),
    )


async def enforce(conn: HTTPConnection) -> None:
    """Global dependency: authenticate, then require the endpoint's capability. Default deny."""
    route = conn.scope.get("route")
    method = "WEBSOCKET" if conn.scope["type"] == "websocket" else conn.scope["method"]
    policy = policy_for(method, getattr(route, "path", conn.scope["path"]))
    if policy is None:
        _raise(
            conn,
            Denied(
                403,
                "unlisted_endpoint",
                "this endpoint is not in the access inventory, so it is denied by default",
            ),
        )
    if policy.public:
        return
    if auth_mode() == "off":
        conn.state.principal = _off_principal()
        return
    address = client_address(conn.scope.get("client"))
    token = _token_from(conn)
    if token is None:
        _raise(
            conn,
            refuse_unidentified(
                address, Denied(401, "unauthenticated", "a Bearer access token is required")
            ),
        )
    principal: Principal | None = None
    try:
        principal = principal_from_claims(await run_in_threadpool(verify_token, token))
        await authorize(principal, "api", method, getattr(route, "path", conn.scope["path"]))
    except Denied as denied:
        if denied.status == 401:
            denied = refuse_unidentified(address, denied)
        if principal is not None and _worth_auditing(denied):
            await run_in_threadpool(
                _record_denial,
                principal.username,
                list(principal.roles),
                method,
                getattr(route, "path", conn.scope["path"]),
                policy.capability,
                "missing capability"
                if denied.code == "forbidden"
                else "the policy engine was unavailable",
            )
        _raise(conn, denied)
    conn.state.principal = principal
