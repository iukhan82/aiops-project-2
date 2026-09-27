"""P11.04: the security checks that only make sense from inside the cluster, run as a Job.

    python backend/target_security.py main       # transport, identity, the API's enforcement matrix, the policy engine
    python backend/target_security.py outage     # with the policy engine STOPPED: a valid, permitted request must be refused

`main` proves, positive and negative, through the real Services and NetworkPolicies:

  TLS      the broker accepts a device with its own certificate and nobody else (no certificate, a certificate from another CA, a client that
           does not trust the broker's CA, plain MQTT bytes); it negotiates TLS 1.2 or better; PostgreSQL is reachable with `verify-full` against
           the platform CA and refuses a connection that is not TLS.
  OIDC     Keycloak issues a token to every operating role by Authorization Code + PKCE and REFUSES the ways around it (the password grant, no
           PKCE challenge, a replayed code); the API refuses no token, a tampered token, an unsigned token and one signed with a key it does not trust.
  API      every GET endpoint of the operator API against every role: a role the inventory allows gets through, every other role is forbidden.
  OPA      the policy engine holds the policy version this release was generated with, permits and forbids as the policy says, and denies by default.

`outage` is run by `verify_security_target.py` after it scales the policy engine to zero. The API asks the engine on every request; with it gone
a request that was permitted a minute ago must now be refused (fail closed), not waved through.

One JSON line on stdout: every check with its detail. Test identities are created in Keycloak with random passwords for the run and deleted at
its end.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import ssl
import sys
import time
import uuid
from pathlib import Path

import httpx
import jwt
import paho.mqtt.client as mqtt
import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from backend import pdp  # noqa: E402
from backend.api import oidc_client  # noqa: E402
from backend.api.authz import inventory  # noqa: E402

CERTS = Path("/devices")
API = os.environ["API_URL"]
KEYCLOAK = os.environ["KEYCLOAK_BASE_URL"]
MQTT_HOST, MQTT_PORT = os.environ["MQTT_HOST"], int(os.environ.get("MQTT_PORT", "8883"))
POSTGRES_HOST = os.environ["POSTGRES_HOST"]
CHECKS: list[dict] = []
SAMPLE_ID = {
    "device_id": "no-such-device", "network_element_type": "segment", "network_element_id": "no-such-element",
    "incident_id": str(uuid.UUID(int=0)), "call_id": str(uuid.UUID(int=0)), "command_id": str(uuid.UUID(int=0)), "outcome_id": str(uuid.UUID(int=0)),
}  # fmt: skip


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})


def path_for(template: str) -> str:
    for key, value in SAMPLE_ID.items():
        template = template.replace("{" + key + "}", value)
    return template


# ------------------------------------------------------------------------------------------------------------------------------- MQTT
def mqtt_connect(cert: str | None, cafile: str = "ca.crt") -> tuple[bool, str]:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sec-{secrets.token_hex(3)}")
    try:
        if cert:
            client.tls_set(
                ca_certs=str(CERTS / cafile),
                certfile=str(CERTS / f"{cert}.crt"),
                keyfile=str(CERTS / f"{cert}.key"),
            )
        else:
            client.tls_set(ca_certs=str(CERTS / cafile))
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=20)
        client.loop_start()
        deadline = time.time() + 8
        while time.time() < deadline and not client.is_connected():
            time.sleep(0.1)
        connected = client.is_connected()
        client.loop_stop()
        if connected:
            client.disconnect()
        return connected, "" if connected else "no CONNACK"
    except (ssl.SSLError, OSError) as exc:
        return False, type(exc).__name__


def transport() -> None:
    ok, _ = mqtt_connect("e2e-device-a")
    check("mqtt_a_device_with_a_certificate_from_the_platform_ca_connects", ok)
    ok, why = mqtt_connect(None)
    check("mqtt_a_client_with_no_certificate_is_refused", not ok, why)
    ok, why = mqtt_connect("rogue")
    check("mqtt_a_certificate_from_another_ca_is_refused", not ok, why)
    ok, why = mqtt_connect("e2e-device-a", cafile="rogue.crt")
    check("mqtt_a_client_that_does_not_trust_the_brokers_ca_refuses_the_broker", not ok, why)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(str(CERTS / "ca.crt"))
    context.load_cert_chain(str(CERTS / "e2e-device-a.crt"), str(CERTS / "e2e-device-a.key"))
    with (
        socket.create_connection((MQTT_HOST, MQTT_PORT), timeout=8) as raw,
        context.wrap_socket(raw, server_hostname="aiops-mqtt-broker") as tls,
    ):
        version = tls.version()
    check("mqtt_negotiates_tls_1_2_or_better", version in ("TLSv1.2", "TLSv1.3"), str(version))

    # a plain MQTT CONNECT (no TLS) sent to the TLS port must not be answered with a CONNACK
    connect_packet = bytes.fromhex("101000044d51545404020014000c706c61696e2d636c69656e74")
    with socket.create_connection((MQTT_HOST, MQTT_PORT), timeout=8) as raw:
        raw.settimeout(5)
        raw.sendall(connect_packet)
        try:
            answer = raw.recv(64)
        except (TimeoutError, ConnectionError, OSError):
            answer = b""
    check(
        "mqtt_plain_mqtt_bytes_on_the_tls_port_get_no_connack",
        not answer.startswith(b"\x20"),
        f"{len(answer)} byte(s) back",
    )

    base = f"host={POSTGRES_HOST} port=5432 dbname={os.environ['POSTGRES_DB']} user={os.environ['POSTGRES_USER']} password={os.environ['POSTGRES_PASSWORD']}"
    try:
        with psycopg.connect(
            f"{base} sslmode=verify-full sslrootcert={CERTS / 'ca.crt'}", connect_timeout=8
        ) as conn:
            in_tls = conn.execute(
                "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
            ).fetchone()[0]
        check(
            "postgres_verify_full_against_the_platform_ca_connects_and_the_session_is_tls",
            bool(in_tls),
        )
    except psycopg.Error as exc:
        check(
            "postgres_verify_full_against_the_platform_ca_connects_and_the_session_is_tls",
            False,
            str(exc)[:200],
        )
    try:
        with psycopg.connect(f"{base} sslmode=disable", connect_timeout=8):
            check(
                "postgres_refuses_a_connection_that_is_not_tls",
                False,
                "a plaintext session was accepted",
            )
    except psycopg.Error as exc:
        check(
            "postgres_refuses_a_connection_that_is_not_tls",
            True,
            str(exc).strip().splitlines()[-1][:120],
        )
    try:
        with psycopg.connect(
            f"{base} sslmode=verify-full sslrootcert={CERTS / 'rogue.crt'}", connect_timeout=8
        ):
            check("postgres_verify_full_against_a_different_ca_fails", False)
    except psycopg.Error:
        check("postgres_verify_full_against_a_different_ca_fails", True)


# ---------------------------------------------------------------------------------------------------------------------------- identity
def admin_headers() -> dict:
    response = httpx.post(
        f"{KEYCLOAK}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": os.environ["KC_ADMIN"],
            "password": os.environ["KC_ADMIN_PASSWORD"],
        },
        timeout=30,
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def make_users(headers: dict) -> dict[str, dict]:
    """One test user per operating role, random password, removed at the end of the run."""
    admin = f"{KEYCLOAK}/admin/realms/aiops"
    users = {}
    for role in inventory()["roles"]:
        username, password = (
            f"p1104-{role.replace('_', '-')}-{secrets.token_hex(2)}",
            secrets.token_urlsafe(18),
        )
        created = httpx.post(
            f"{admin}/users", headers=headers, timeout=30,
            json={"username": username, "enabled": True, "emailVerified": True, "firstName": "P1104", "lastName": role, "email": f"{username}@example.test"},
        )  # fmt: skip
        created.raise_for_status()
        user_id = created.headers["Location"].rsplit("/", 1)[1]
        httpx.put(
            f"{admin}/users/{user_id}/reset-password",
            headers=headers,
            timeout=30,
            json={"type": "password", "value": password, "temporary": False},
        ).raise_for_status()
        role_repr = httpx.get(f"{admin}/roles/{role}", headers=headers, timeout=30).json()
        httpx.post(
            f"{admin}/users/{user_id}/role-mappings/realm",
            headers=headers,
            timeout=30,
            json=[role_repr],
        ).raise_for_status()
        users[role] = {"id": user_id, "username": username, "password": password}
    return users


def delete_users(headers: dict, users: dict[str, dict]) -> None:
    for user in users.values():
        httpx.delete(
            f"{KEYCLOAK}/admin/realms/aiops/users/{user['id']}", headers=headers, timeout=30
        )


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def identity_and_api(users: dict[str, dict]) -> dict[str, str]:
    tokens: dict[str, str] = {}
    failures = []
    for role, user in users.items():
        try:
            tokens[role] = oidc_client.login(user["username"], user["password"]).access_token
        except oidc_client.LoginFailed as exc:
            failures.append(f"{role}: {exc}")
    check(
        "keycloak_issues_a_token_to_every_operating_role_by_authorization_code_and_pkce",
        not failures and len(tokens) == len(users),
        f"{len(tokens)}/{len(users)} roles; {failures[:2]}",
    )

    # ways around it, each of which must be refused
    password_grant = httpx.post(f"{KEYCLOAK}/realms/aiops/protocol/openid-connect/token", timeout=30,
                                data={"grant_type": "password", "client_id": "aiops-ui", "username": next(iter(users.values()))["username"], "password": next(iter(users.values()))["password"]})  # fmt: skip
    check(
        "keycloak_refuses_the_password_grant_for_the_console_client",
        password_grant.status_code >= 400,
        f"status {password_grant.status_code}",
    )
    with httpx.Client(timeout=30) as client:
        page = oidc_client.authorization_request(client, None, None)
        check(
            "keycloak_refuses_an_authorization_request_with_no_pkce_challenge",
            page.status_code != 200 or "kc-form-login" not in page.text,
            f"status {page.status_code}",
        )
    user = next(iter(users.values()))
    verifier, challenge = oidc_client.pkce_pair()
    with httpx.Client(timeout=30) as client:
        page = oidc_client.authorization_request(client, challenge)
        posted = oidc_client.submit_credentials(client, page, user["username"], user["password"])
        code = oidc_client.code_from(posted)
        first = oidc_client.exchange_code(client, code, verifier)
        replay = oidc_client.exchange_code(client, code, verifier)
    check(
        "keycloak_refuses_a_replayed_authorization_code",
        first.status_code == 200 and replay.status_code >= 400,
        f"first {first.status_code}, replay {replay.status_code}",
    )

    # the API and its tokens
    me_ok = all(
        httpx.get(f"{API}/api/v1/me", headers={"Authorization": f"Bearer {t}"}, timeout=30)
        .json()
        .get("roles")
        == [r]
        for r, t in tokens.items()
    )
    check("the_api_reports_each_token_as_exactly_its_own_role", me_ok)
    check(
        "the_api_refuses_a_request_with_no_token",
        httpx.get(f"{API}/api/v1/me", timeout=30).status_code == 401,
    )
    sample = next(iter(tokens.values()))
    head, body, sig = sample.split(".")
    tampered = f"{head}.{b64(json.dumps({**json.loads(base64.urlsafe_b64decode(body + '==')), 'realm_access': {'roles': ['supervisor']}}).encode())}.{sig}"
    check(
        "the_api_refuses_a_token_whose_payload_was_altered",
        httpx.get(
            f"{API}/api/v1/me", headers={"Authorization": f"Bearer {tampered}"}, timeout=30
        ).status_code
        == 401,
    )
    claims = jwt.decode(sample, options={"verify_signature": False})
    unsigned = jwt.encode(claims, key=None, algorithm="none")
    check(
        "the_api_refuses_an_unsigned_token",
        httpx.get(
            f"{API}/api/v1/me", headers={"Authorization": f"Bearer {unsigned}"}, timeout=30
        ).status_code
        == 401,
    )
    foreign = jwt.encode(claims, secrets.token_bytes(32), algorithm="HS256")
    check(
        "the_api_refuses_a_token_signed_with_a_key_it_does_not_trust",
        httpx.get(
            f"{API}/api/v1/me", headers={"Authorization": f"Bearer {foreign}"}, timeout=30
        ).status_code
        == 401,
    )

    # the enforcement matrix: every GET endpoint of the operator API against every role, the expectation read from the inventory
    inv = inventory()
    endpoints = [
        a
        for a in inv["apis"]
        if a["method"] == "GET"
        and a.get("service", "api") == "api"
        and a["status"] == "implemented"
        and not a.get("public")
        and a["path"] != "/api/v1/me"
    ]
    wrong, cells = [], 0
    for endpoint in endpoints:
        allowed = (
            set(inv["capabilities"][endpoint["capability"]]["roles"])
            if endpoint["capability"]
            else set(inv["roles"])
        )
        for role, token in tokens.items():
            status = httpx.get(
                f"{API}{path_for(endpoint['path'])}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=60,
            ).status_code
            cells += 1
            if role in allowed and status in (401, 403):
                wrong.append(f"{role} refused on {endpoint['path']}: {status}")
            if role not in allowed and status != 403:
                wrong.append(f"{role} not refused on {endpoint['path']}: {status}")
    check(
        "every_get_endpoint_lets_the_roles_the_inventory_allows_through_and_forbids_every_other_role",
        not wrong and cells > 100,
        f"{len(endpoints)} endpoints x {len(tokens)} roles = {cells} cells; wrong {wrong[:3]}",
    )
    return tokens


# -------------------------------------------------------------------------------------------------------------------------------- policy
def policy() -> None:
    data = json.loads(
        (SOURCE_ROOT / "policy" / "aiops" / "model" / "data.json").read_text(encoding="utf-8")
    )
    version = pdp.loaded_version()
    check(
        "the_policy_engine_holds_the_policy_version_this_release_was_generated_with",
        version == data.get("version"),
        f"engine {version}",
    )
    read = pdp.api_decision("api", "GET", "/api/v1/incidents", ["operator"])
    check(
        "the_policy_permits_a_role_the_inventory_allows",
        read.get("allow") is True,
        str(read.get("reason")),
    )
    write = pdp.api_decision("api", "POST", "/api/v1/commands", ["auditor"])
    check(
        "the_policy_forbids_a_role_the_inventory_does_not_allow",
        write.get("allow") is False,
        str(write.get("reason")),
    )
    unlisted = pdp.api_decision("api", "GET", "/api/v1/not-a-route", ["supervisor"])
    check(
        "the_policy_denies_an_endpoint_that_is_not_listed_by_default",
        unlisted.get("allow") is False,
        str(unlisted.get("reason")),
    )
    nobody = pdp.api_decision("api", "GET", "/api/v1/incidents", [])
    check(
        "the_policy_denies_a_caller_with_no_operating_role",
        nobody.get("allow") is False,
        str(nobody.get("reason")),
    )
    invented = pdp.api_decision("api", "GET", "/api/v1/incidents", ["superuser"])
    check(
        "the_policy_gives_an_invented_role_nothing",
        invented.get("allow") is False,
        str(invented.get("reason")),
    )


def outage(tokens_user: dict) -> None:
    token = oidc_client.login(tokens_user["username"], tokens_user["password"]).access_token
    try:
        status = httpx.get(
            f"{API}/api/v1/incidents", headers={"Authorization": f"Bearer {token}"}, timeout=30
        ).status_code
    except httpx.HTTPError as exc:
        status = 0
        detail = type(exc).__name__
    else:
        detail = f"status {status}"
    check(
        "with_the_policy_engine_stopped_a_permitted_request_is_refused_not_waved_through",
        status in (0, 403, 500, 502, 503),
        detail,
    )


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "main"
    headers = admin_headers()
    users = make_users(headers)
    try:
        if phase == "main":
            transport()
            identity_and_api(users)
            policy()
        else:
            outage(users["operator"])
    finally:
        delete_users(headers, users)
    passed = all(c["ok"] for c in CHECKS)
    print(json.dumps({"phase": phase, "passed": passed, "checks": CHECKS}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
