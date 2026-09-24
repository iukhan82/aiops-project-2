"""P08.04 acceptance evidence (API side), against the real Keycloak and the real
FastAPI app under uvicorn on a real socket:

    python source-code/backend/api/verify_auth.py

Every token is obtained the way the browser gets one - Authorization Code with
PKCE against the real realm - and presented to the real API. The enforcement
matrix is generated from the UX inventory itself (every implemented GET endpoint
against every role), so "the API enforces exactly what the inventory says" is
tested, not sampled. Refusals a real Keycloak cannot be made to issue (an
expired token, `alg=none`, HS256 key confusion, a wrong issuer) are exercised
with tokens signed by a locally generated key that the app is temporarily
pointed at; the same refusal logic is unit-tested in tests/test_auth.py.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import types
import uuid
from pathlib import Path

import httpx
import jwt
import uvicorn
import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

os.environ.pop(
    "AIOPS_AUTH_MODE", None
)  # this script proves authentication, so it must run with it on

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.api import auth, oidc_client  # noqa: E402
from backend.api.app import app  # noqa: E402
from backend.api.authz import capabilities_of, inventory  # noqa: E402
from backend.evidence import Evidence  # noqa: E402

HOST, PORT = "127.0.0.1", 8811
BASE = f"http://{HOST}:{PORT}"
IDENTITIES = json.loads(
    (SOURCE_ROOT / "infra" / "platform" / "output" / "demo_identities.json").read_text(
        encoding="utf-8"
    )
)
SERVICE_SECRETS = json.loads(
    (SOURCE_ROOT / "infra" / "platform" / "output" / "service_client_secrets.json").read_text(
        encoding="utf-8"
    )
)
ROLE_USER = {spec["role"]: user for user, spec in reversed(list(IDENTITIES.items()))}
ev = Evidence("P08.04")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
SAMPLE_ID = {
    "device_id": "no-such-device",
    "network_element_type": "segment",
    "network_element_id": "no-such-element",
    "incident_id": str(uuid.UUID(int=0)),
    "call_id": str(uuid.UUID(int=0)),
    "command_id": str(uuid.UUID(int=0)),
    "outcome_id": str(uuid.UUID(int=0)),
}


def run_server() -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(80):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError("uvicorn did not start")


def crafted(**overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": auth.ISSUER,
        "aud": auth.AUDIENCE,
        "sub": "crafted",
        "iat": now,
        "exp": now + 300,
        "azp": "aiops-ui",
        "preferred_username": "crafted",
        "realm_access": {"roles": ["operator"]},
    }
    for k, v in overrides.items():
        claims.pop(k, None) if v is None else claims.__setitem__(k, v)
    return jwt.encode(claims, PEM, algorithm="RS256")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def path_for(template: str) -> str:
    out = template
    for name, value in SAMPLE_ID.items():
        out = out.replace("{" + name + "}", value)
    return out


async def main() -> int:  # noqa: PLR0915
    inv = inventory()
    tokens = {
        role: oidc_client.login(user, IDENTITIES[user]["password"])
        for role, user in ROLE_USER.items()
    }
    server = run_server()
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:

            def bearer(t: str) -> dict:
                return {"Authorization": f"Bearer {t}"}

            health = (await client.get("/api/v1/health")).json()
            ev.check(
                "health_is_public_and_reports_authentication_is_on_and_the_database_reachable",
                health["auth_mode"] == "oidc" and health["database"] == "ok",
                detail=str(health),
            )
            missing = await client.get("/api/v1/devices")
            ev.check(
                "a_protected_endpoint_without_a_token_is_401_with_a_bearer_challenge",
                missing.status_code == 401
                and "Bearer" in missing.headers.get("www-authenticate", ""),
                detail=str(missing.json()),
            )
            ev.check(
                "me_without_a_token_is_401", (await client.get("/api/v1/me")).status_code == 401
            )
            ev.check(
                "a_garbage_token_is_401",
                (await client.get("/api/v1/devices", headers=bearer("garbage"))).status_code == 401,
            )

            # ---- /me for every role, from real Keycloak logins ----
            me_ok = {}
            for role, t in tokens.items():
                me = (await client.get("/api/v1/me", headers=bearer(t.access_token))).json()
                me_ok[role] = (
                    me["roles"] == [role]
                    and set(me["capabilities"]) == set(capabilities_of((role,)))
                    and me["home"] == inv["roles"][role]["home"]
                    and me["auth_mode"] == "oidc"
                    and me["expires_at"] - int(time.time()) <= 300
                )
            ev.check(
                "me_reports_exactly_the_real_role_the_capabilities_it_adds_up_to_and_the_home_screen",
                all(me_ok.values()),
                detail=str(me_ok),
            )
            claims = {role: t.claims for role, t in tokens.items()}
            ev.check(
                "real_tokens_are_short_lived_audience_bound_and_carry_only_the_operating_role_nothing_else",
                all(
                    c["exp"] - c["iat"] <= 300
                    and c["aud"] == "aiops-api"
                    and c["azp"] == "aiops-ui"
                    and c["realm_access"]["roles"] == [r]
                    and c["iss"] == auth.ISSUER
                    for r, c in claims.items()
                ),
                detail=str({r: c["realm_access"]["roles"] for r, c in claims.items()}),
            )

            # ---- the enforcement matrix, generated from the inventory ----
            checked, mismatches = 0, []
            gets = [
                a
                for a in inv["apis"]
                if a["method"] == "GET"
                and a.get("service", "api") == "api"
                and a["status"] == "implemented"
                and a["id"] not in ("me", "health")
            ]
            for api in gets:
                for role, t in tokens.items():
                    r = await client.get(path_for(api["path"]), headers=bearer(t.access_token))
                    allowed = api["capability"] in capabilities_of((role,))
                    ok = (r.status_code == 403) if not allowed else r.status_code not in (401, 403)
                    checked += 1
                    if not ok:
                        mismatches.append(
                            f"{api['id']} as {role}: {r.status_code} (allowed={allowed})"
                        )
            ev.check(
                "the_api_enforces_exactly_the_inventorys_capability_for_every_implemented_get_endpoint_and_every_role",
                not mismatches and checked >= 100,
                detail=f"{checked} endpoint-role combinations; {mismatches[:3]}",
            )
            ev.metrics["enforcement_matrix"] = {
                "endpoints": len(gets),
                "roles": len(tokens),
                "combinations": checked,
                "mismatches": len(mismatches),
            }
            forbidden = (
                await client.get(
                    "/api/v1/commands", headers=bearer(tokens["field_responder"].access_token)
                )
            ).json()["detail"]
            ev.check(
                "a_403_names_the_missing_capability_and_the_callers_roles",
                forbidden.get("capability") == "commands.view"
                and forbidden.get("roles") == ["field_responder"],
                detail=str(forbidden),
            )

            # ---- real tokens that must be refused ----
            operator = tokens["operator"].access_token
            head, payload, sig = operator.split(".")
            tampered = f"{head}.{payload[:-4]}AAAA.{sig}"
            ev.check(
                "a_real_token_with_a_tampered_payload_is_refused",
                (await client.get("/api/v1/devices", headers=bearer(tampered))).status_code == 401,
            )
            other_audience = httpx.post(
                oidc_client.TOKEN_URL,
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": "alex.chen",
                    "password": IDENTITIES["alex.chen"]["password"],
                },
                timeout=30,
            )
            wrong_aud = (
                await client.get(
                    "/api/v1/devices", headers=bearer(other_audience.json()["access_token"])
                )
                if other_audience.status_code == 200
                else None
            )
            ev.check(
                "a_real_token_issued_by_the_realm_to_another_client_with_no_api_audience_is_refused",
                wrong_aud is not None
                and wrong_aud.status_code == 401
                and wrong_aud.json()["detail"]["error"] in ("invalid_audience", "invalid_token"),
                detail=str(wrong_aud.json())
                if wrong_aud is not None
                else f"could not obtain the token: {other_audience.status_code}",
            )
            service = oidc_client.client_credentials(
                "system-command-executor", SERVICE_SECRETS["system-command-executor"]
            )
            svc_me = (await client.get("/api/v1/me", headers=bearer(service.access_token))).json()
            svc_devices = await client.get("/api/v1/devices", headers=bearer(service.access_token))
            ev.check(
                "the_command_executor_service_identity_authenticates_but_holds_no_ui_capability",
                svc_me["roles"] == ["system:command-executor"]
                and svc_me["capabilities"] == []
                and svc_devices.status_code == 403,
                detail=str(svc_me),
            )

            # ---- refusals a real Keycloak cannot be made to issue: locally signed tokens, app pointed at the local key ----
            real_client = auth._jwks_client  # noqa: SLF001
            auth._jwks_client = lambda: types.SimpleNamespace(
                get_signing_key_from_jwt=lambda _t: types.SimpleNamespace(key=KEY.public_key())
            )  # noqa: SLF001
            try:

                async def code_of(raw: str, path: str = "/api/v1/me") -> tuple[int, str]:
                    r = await client.get(path, headers=bearer(raw))
                    detail = r.json().get("detail", {}) if r.status_code >= 400 else {}
                    return r.status_code, detail.get("error", "")

                ev.check(
                    "control_a_locally_signed_valid_token_is_accepted_so_the_refusals_below_are_about_the_claims_not_the_setup",
                    (await code_of(crafted()))[0] == 200,
                )
                results = {
                    "expired": await code_of(crafted(exp=int(time.time()) - 60)),
                    "wrong_issuer": await code_of(crafted(iss="http://evil.example/realms/aiops")),
                    "wrong_audience": await code_of(crafted(aud="account")),
                    "unknown_client": await code_of(crafted(azp="some-other-app")),
                    "no_operating_role": await code_of(
                        crafted(realm_access={"roles": ["offline_access"]})
                    ),
                    "missing_exp": await code_of(crafted(exp=None)),
                }
                header = b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
                body = b64(
                    json.dumps(
                        {
                            "iss": auth.ISSUER,
                            "aud": auth.AUDIENCE,
                            "sub": "x",
                            "exp": int(time.time()) + 300,
                            "azp": "aiops-ui",
                            "realm_access": {"roles": ["operator"]},
                        }
                    ).encode()
                )
                results["alg_none"] = await code_of(f"{header}.{body}.")
                public_pem = KEY.public_key().public_bytes(
                    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
                )
                h256 = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
                results["hs256_key_confusion"] = await code_of(
                    f"{h256}.{body}.{b64(hmac.new(public_pem, f'{h256}.{body}'.encode(), hashlib.sha256).digest())}"
                )
                expected = {
                    "expired": (401, "token_expired"),
                    "wrong_issuer": (401, "invalid_issuer"),
                    "wrong_audience": (401, "invalid_audience"),
                    "unknown_client": (401, "invalid_client"),
                    "no_operating_role": (403, "no_operating_role"),
                    "missing_exp": (401, "invalid_token"),
                    "alg_none": (401, "invalid_token"),
                    "hs256_key_confusion": (401, "invalid_token"),
                }
                ev.check(
                    "expired_wrong_issuer_wrong_audience_unknown_client_missing_claim_alg_none_and_key_confusion_are_each_refused_with_the_right_reason",
                    results == expected,
                    detail=str({k: v for k, v in results.items() if v != expected[k]})
                    or str(results),
                )

                def down(_t: str):
                    raise jwt.PyJWKClientConnectionError("identity provider unreachable")

                auth._jwks_client = lambda: types.SimpleNamespace(get_signing_key_from_jwt=down)  # noqa: SLF001
                ev.check(
                    "an_unreachable_identity_provider_fails_closed_503_never_open",
                    (await code_of(crafted(), "/api/v1/devices"))
                    == (503, "identity_provider_unavailable"),
                )
            finally:
                auth._jwks_client = real_client  # noqa: SLF001

            # ---- default deny: a route nobody listed ----
            app.add_api_route("/api/v1/_unlisted_probe", lambda: {"leaked": True}, methods=["GET"])
            try:
                probe = await client.get("/api/v1/_unlisted_probe", headers=bearer(operator))
                ev.check(
                    "a_route_missing_from_the_inventory_is_denied_by_default",
                    probe.status_code == 403
                    and probe.json()["detail"]["error"] == "unlisted_endpoint",
                )
            finally:
                app.router.routes = [
                    r
                    for r in app.router.routes
                    if getattr(r, "path", "") != "/api/v1/_unlisted_probe"
                ]

        # ---- the live WebSocket: the token rides in the query string ----
        async def ws_result(query: str) -> str:
            try:
                async with websockets.connect(
                    f"ws://{HOST}:{PORT}/api/v1/live{query}", open_timeout=10
                ):
                    return "connected"
            except websockets.exceptions.InvalidStatus as exc:
                return f"refused {exc.response.status_code}"
            except websockets.exceptions.WebSocketException as exc:
                return f"closed {type(exc).__name__}"

        ws = {
            "no_token": await ws_result(""),
            "garbage": await ws_result("?access_token=garbage"),
            "operator": await ws_result(f"?access_token={tokens['operator'].access_token}"),
            "field_responder": await ws_result(
                f"?access_token={tokens['field_responder'].access_token}"
            ),
            "service": await ws_result(f"?access_token={service.access_token}"),
        }
        ev.check(
            "the_live_websocket_needs_a_valid_token_with_map_view",
            ws["no_token"].startswith("refused")
            and ws["garbage"].startswith("refused")
            and ws["service"].startswith("refused")
            and ws["operator"] == "connected"
            and ws["field_responder"] == "connected",
            detail=str(ws),
        )

        # ---- the interactive flow's own hardening is proven in the identity-provider verification; here, the API side of logout ----
        end = oidc_client.end_session(tokens["auditor"].id_token, tokens["auditor"].refresh_token)
        refreshed = oidc_client.refresh(tokens["auditor"].refresh_token)
        ev.check(
            "after_logout_the_refresh_token_no_longer_works",
            end.status_code in (200, 204, 302) and refreshed.status_code == 400,
            detail=f"logout {end.status_code}, refresh {refreshed.status_code}",
        )
    finally:
        server.should_exit = True
        await asyncio.sleep(0.5)
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
