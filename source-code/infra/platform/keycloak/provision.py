"""P09.02: create the demo identities in the imported `aiops` realm.

    python source-code/infra/platform/keycloak/provision.py

The realm itself (roles, the `aiops-ui` public PKCE client, the `aiops-api`
audience, service clients) is imported from `realm/aiops-realm.json` when the
container starts and contains no secret. What this script adds is what must
never be committed:

* one demo user per operating role, each with a **randomly generated password**
  (`secrets`), written only to the git-ignored
  `source-code/infra/platform/output/demo_identities.json`;
* the generated client secrets of the service clients, written to the git-ignored
  `service_client_secrets.json` beside it.

It is idempotent: a user that already exists keeps its password if the local file
still records it, and is reset (new random password) if the file is missing, so a
lost file is recoverable without editing Keycloak by hand. Admin credentials come
from `.env`, also git-ignored.
"""

from __future__ import annotations

import json
import secrets
import sys
import time
from pathlib import Path

import httpx

PLATFORM = Path(__file__).resolve().parents[1]
OUTPUT = PLATFORM / "output"
IDENTITIES = OUTPUT / "demo_identities.json"
SERVICE_SECRETS = OUTPUT / "service_client_secrets.json"
BASE = "http://127.0.0.1:8180"
REALM = "aiops"

DEMO_USERS = [
    ("alex.chen", "Alex Chen", "operator"),
    ("sam.okafor", "Sam Okafor", "supervisor"),
    ("dana.rivera", "Dana Rivera", "dispatcher"),
    ("eve.laurent", "Eve Laurent", "incident_commander"),
    ("fin.hassan", "Fin Hassan", "field_responder"),
    ("ana.petrov", "Ana Petrov", "auditor"),
    ("dee.moreno", "Dee Moreno", "demo_operator"),
    (
        "sam.two",
        "Sam Two",
        "supervisor",
    ),  # a second supervisor: four-eyes needs two different people
    ("alex.two", "Alex Two", "operator"),
]
SERVICE_CLIENTS = [
    "system-command-executor",
    "system-outcome-verifier",
    "system-optimization-engine",
    "system-emergency-engine",
    "system-aiops-remediation",
]


def read_env() -> dict[str, str]:
    values = {}
    for line in (PLATFORM / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def wait_until_ready(client: httpx.Client, timeout_s: float = 240.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if (
                client.get(f"{BASE}/realms/{REALM}/.well-known/openid-configuration").status_code
                == 200
            ):
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise SystemExit("Keycloak did not become ready with the aiops realm imported")


def admin_token(client: httpx.Client, env: dict[str, str]) -> str:
    r = client.post(
        f"{BASE}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": env["KEYCLOAK_ADMIN"],
            "password": env["KEYCLOAK_ADMIN_PASSWORD"],
        },
    )
    r.raise_for_status()
    return r.json()["access_token"]


def sync_ui_client(client: httpx.Client, admin: str, headers: dict) -> None:
    """The realm file is only imported into an empty Keycloak; this makes an already-running realm converge on the
    UI client's redirect/origin/PKCE settings in that file, so tightening them there takes effect without a reset."""
    realm = json.loads(
        (Path(__file__).parent / "realm" / "aiops-realm.json").read_text(encoding="utf-8")
    )
    wanted = next(c for c in realm["clients"] if c["clientId"] == "aiops-ui")
    current = client.get(
        f"{admin}/clients", params={"clientId": "aiops-ui"}, headers=headers
    ).json()[0]
    merged = {
        **current,
        **{k: wanted[k] for k in ("redirectUris", "webOrigins")},
        "attributes": {**current.get("attributes", {}), **wanted["attributes"]},
    }
    if merged != current:
        client.put(
            f"{admin}/clients/{current['id']}", json=merged, headers=headers
        ).raise_for_status()
        print("  aiops-ui client settings synced from the realm file")


def main() -> int:
    env = read_env()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    previous = json.loads(IDENTITIES.read_text(encoding="utf-8")) if IDENTITIES.is_file() else {}
    with httpx.Client(timeout=30) as client:
        wait_until_ready(client)
        headers = {"Authorization": f"Bearer {admin_token(client, env)}"}
        admin = f"{BASE}/admin/realms/{REALM}"
        sync_ui_client(client, admin, headers)

        identities: dict[str, dict] = {}
        for username, full_name, role in DEMO_USERS:
            first, _, last = full_name.partition(" ")
            found = client.get(
                f"{admin}/users", params={"username": username, "exact": "true"}, headers=headers
            ).json()
            password = previous.get(username, {}).get("password") if found else None
            if found and password:
                user_id = found[0]["id"]
                action = "kept"
            else:
                password = secrets.token_urlsafe(18)
                body = {
                    "username": username,
                    "enabled": True,
                    "emailVerified": True,
                    "firstName": first,
                    "lastName": last,
                    "email": f"{username}@example.test",
                }
                if found:
                    user_id = found[0]["id"]
                    action = "password reset"
                else:
                    r = client.post(f"{admin}/users", json=body, headers=headers)
                    r.raise_for_status()
                    user_id = r.headers["Location"].rsplit("/", 1)[1]
                    action = "created"
                client.put(
                    f"{admin}/users/{user_id}/reset-password",
                    json={"type": "password", "value": password, "temporary": False},
                    headers=headers,
                ).raise_for_status()
            role_repr = client.get(f"{admin}/roles/{role}", headers=headers).json()
            client.post(
                f"{admin}/users/{user_id}/role-mappings/realm", json=[role_repr], headers=headers
            ).raise_for_status()
            identities[username] = {"role": role, "name": full_name, "password": password}
            print(f"  {username:12s} {role:20s} {action}")
        IDENTITIES.write_text(json.dumps(identities, indent=2), encoding="utf-8")

        client_secrets: dict[str, str] = {}
        for cid in SERVICE_CLIENTS:
            found = client.get(f"{admin}/clients", params={"clientId": cid}, headers=headers).json()
            if not found:
                raise SystemExit(f"service client {cid} missing: the realm import did not run")
            secret = client.get(
                f"{admin}/clients/{found[0]['id']}/client-secret", headers=headers
            ).json()["value"]
            client_secrets[cid] = secret
        SERVICE_SECRETS.write_text(json.dumps(client_secrets, indent=2), encoding="utf-8")
    print(
        f"wrote {IDENTITIES.name} and {SERVICE_SECRETS.name} to {OUTPUT} (git-ignored; contain generated secrets)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
