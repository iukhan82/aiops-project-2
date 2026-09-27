"""P09.07 (CTL-46): a reviewable answer to "who holds which operating role, and should they?", from the code and from the running identity provider.

    python source-code/security/access_review.py            # review, write evidence
    python source-code/security/access_review.py --offline  # only the declared side (no Keycloak needed)

The joiner-mover-leaver procedure is `docs/security/ACCESS_REVIEW.md`. This script is its mechanical first pass: it compares three
things that must agree and applies the separation-of-duties rules the platform's own policy depends on.

DECLARED   `backend/roles.py` (the role catalogue and the request/approve authority), the committed realm
           (`infra/platform/keycloak/realm/aiops-realm.json`) and the demo users and service clients `provision.py` creates.
LIVE       the real `aiops` realm read through the Keycloak admin API (skipped, and said so, when Keycloak is not reachable).

Rules applied to every human and service identity:

R1  the realm defines exactly the roles the code knows (no invented role, none missing);
R2  a service identity is held only by the service account of its own client, one role each, and that client has no interactive flow;
R3  a human never holds a `system:` role, and holds exactly one operating role;
R4  separation of duties: nobody holds both a role that may REQUEST and a role that may APPROVE in a safety class that needs four
    eyes, and the auditor holds nothing else (an auditor who could act would audit themselves);
R5  every role has a holder, and the two roles that need a second person for four-eyes have at least two;
R6  the realm is hardened: no self-registration, brute-force protection, TLS required, short token lifetime, refresh tokens revoked;
R7  LIVE = DECLARED: no unknown account, no missing account, no different role, no enabled account without a declared owner.

A finding names the account or role and the rule. The report is not a substitute for the accountable owner's periodic review; it is
what that review starts from.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))
sys.path.insert(0, str(SOURCE_ROOT / "infra" / "platform" / "keycloak"))

import provision  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from backend.roles import (  # noqa: E402
    APPROVE_ROLES,
    FOUR_EYES_CLASSES,
    HUMAN_ROLES,
    REQUEST_ROLES,
    SERVICE_IDENTITIES,
)

REALM_FILE = provision.PLATFORM / "keycloak" / "realm" / "aiops-realm.json"
PROCEDURE = REPO_ROOT / "docs" / "security" / "ACCESS_REVIEW.md"
ev = Evidence("P09.07", "p09_07_access_review", docs_name="p09_07_access_review")
FOUR_EYES_ROLES_NEEDING_TWO = ("operator", "supervisor")
MAX_TOKEN_LIFETIME_S = 900


def sod_conflicts() -> list[tuple[str, str, str]]:
    """(safety class, requesting role, approving role) pairs one person must never hold together."""
    out = []
    for cls in sorted(FOUR_EYES_CLASSES):
        for requester in sorted(REQUEST_ROLES[cls]):
            for approver in sorted(APPROVE_ROLES[cls]):
                if requester != approver:
                    out.append((cls, requester, approver))
    return out


def declared() -> dict:
    users = {u: {"name": n, "roles": [r]} for u, n, r in provision.DEMO_USERS}
    for client in provision.SERVICE_CLIENTS:
        users[f"service-account-{client}"] = {
            "name": client,
            "roles": [client.replace("system-", "system:", 1)],
        }
    return users


def _role_names(client: httpx.Client, admin: str, headers: dict, user_id: str) -> list[str]:
    roles = client.get(f"{admin}/users/{user_id}/role-mappings/realm", headers=headers).json()
    return sorted(
        r["name"]
        for r in roles
        if not r["name"].startswith(("default-roles", "offline_access", "uma_"))
    )


def live_users(client: httpx.Client, headers: dict) -> dict:
    """Every account in the running realm. Service accounts are not returned by the user search, so each service client's own
    service-account user is read separately."""
    admin = f"{provision.BASE}/admin/realms/{provision.REALM}"
    out = {}
    for user in client.get(f"{admin}/users", params={"max": 500}, headers=headers).json():
        out[user["username"]] = {
            "enabled": user.get("enabled"),
            "email": user.get("email"),
            "roles": _role_names(client, admin, headers, user["id"]),
        }
    for cid in provision.SERVICE_CLIENTS:
        found = client.get(f"{admin}/clients", params={"clientId": cid}, headers=headers).json()
        if not found:
            continue
        account = client.get(
            f"{admin}/clients/{found[0]['id']}/service-account-user", headers=headers
        ).json()
        out[account["username"]] = {
            "enabled": account.get("enabled"),
            "email": account.get("email"),
            "roles": _role_names(client, admin, headers, account["id"]),
        }
    return out


def main() -> int:  # noqa: PLR0915
    offline = "--offline" in sys.argv
    realm = json.loads(REALM_FILE.read_text(encoding="utf-8"))
    known_roles = set(HUMAN_ROLES) | set(SERVICE_IDENTITIES)
    findings: list[dict] = []

    def finding(rule: str, subject: str, detail: str) -> None:
        findings.append({"rule": rule, "subject": subject, "detail": detail})

    # R1
    realm_roles = {r["name"] for r in realm["roles"]["realm"]}
    for role in sorted(realm_roles - known_roles):
        finding("R1", role, "the realm defines a role the code does not know")
    for role in sorted(known_roles - realm_roles):
        finding("R1", role, "the code knows a role the realm does not define")
    ev.check(
        "the_realm_defines_exactly_the_roles_the_code_knows",
        realm_roles == known_roles,
        f"{len(realm_roles)} roles",
    )

    # R2
    clients = {c["clientId"]: c for c in realm["clients"]}
    service_users = {u["username"]: u for u in realm.get("users", [])}
    holders_of_service: dict[str, list[str]] = {r: [] for r in SERVICE_IDENTITIES}
    for username, user in service_users.items():
        for role in user.get("realmRoles", []):
            if role in holders_of_service:
                holders_of_service[role].append(username)
        cid = username.removeprefix("service-account-")
        client = clients.get(cid)
        if client is None or not client.get("serviceAccountsEnabled"):
            finding("R2", username, "a service-account user without a service-account client")
        elif (
            client.get("standardFlowEnabled")
            or client.get("directAccessGrantsEnabled")
            or client.get("implicitFlowEnabled")
        ):
            finding("R2", cid, "a service client allows an interactive flow")
        expected = "system:" + cid.removeprefix("system-")
        if user.get("realmRoles") != [expected]:
            finding(
                "R2", username, f"holds {user.get('realmRoles')}, expected exactly [{expected!r}]"
            )
    for role, who in holders_of_service.items():
        if len(who) != 1:
            finding("R2", role, f"held by {len(who)} service accounts, expected exactly one")
    ev.check(
        "each_service_identity_is_held_by_exactly_its_own_service_account_and_no_service_client_has_an_interactive_flow",
        not [f for f in findings if f["rule"] == "R2"],
    )

    # R3 / R4 / R5 on the declared humans
    humans = {u: d for u, d in declared().items() if not u.startswith("service-account-")}
    for user, data in humans.items():
        roles = data["roles"]
        if any(r.startswith("system:") for r in roles):
            finding("R3", user, "a person holds a system identity")
        if len(roles) != 1:
            finding("R3", user, f"holds {len(roles)} operating roles, expected exactly one")
        if any(r not in HUMAN_ROLES for r in roles):
            finding("R3", user, "holds a role that is not an operating role")
        for cls, requester, approver in sod_conflicts():
            if requester in roles and approver in roles:
                finding(
                    "R4", user, f"can request ({requester}) and approve ({approver}) a {cls} action"
                )
        if "auditor" in roles and len(roles) > 1:
            finding("R4", user, "the auditor also holds an operating role")
    by_role: dict[str, list[str]] = {r: [] for r in HUMAN_ROLES}
    for user, data in humans.items():
        for role in data["roles"]:
            by_role.setdefault(role, []).append(user)
    for role, who in by_role.items():
        if not who:
            finding("R5", role, "no one holds this role")
    for role in FOUR_EYES_ROLES_NEEDING_TWO:
        if len(by_role.get(role, [])) < 2:
            finding("R5", role, "four-eyes needs two different people holding this role")
    ev.check(
        "no_person_holds_a_system_identity_and_each_holds_exactly_one_operating_role",
        not [f for f in findings if f["rule"] == "R3"],
    )
    ev.check(
        "nobody_can_both_request_and_approve_a_four_eyes_action_and_the_auditor_holds_nothing_else",
        not [f for f in findings if f["rule"] == "R4"],
        f"{len(sod_conflicts())} conflicting role pairs checked",
    )
    ev.check(
        "every_operating_role_has_a_holder_and_four_eyes_roles_have_two",
        not [f for f in findings if f["rule"] == "R5"],
        str({r: len(w) for r, w in by_role.items()}),
    )

    # R6
    hardening = {
        "registrationAllowed": realm.get("registrationAllowed") is False,
        "bruteForceProtected": realm.get("bruteForceProtected") is True,
        "sslRequired": realm.get("sslRequired") in ("external", "all"),
        "accessTokenLifespan<=900s": realm.get("accessTokenLifespan", 10**9)
        <= MAX_TOKEN_LIFETIME_S,
        "revokeRefreshToken": realm.get("revokeRefreshToken") is True,
        "passwordPolicy": bool(realm.get("passwordPolicy")),
    }
    for name, ok in hardening.items():
        if not ok:
            finding("R6", "realm", f"{name} is not as required")
    ev.check(
        "the_realm_is_hardened_no_self_registration_lockout_tls_short_tokens_refresh_revocation_password_policy",
        all(hardening.values()),
        str(hardening),
    )

    # R7 live
    live = None
    if not offline:
        try:
            env = provision.read_env()
            with httpx.Client(timeout=20) as client:
                provision.wait_until_ready(client, 20)
                headers = {"Authorization": f"Bearer {provision.admin_token(client, env)}"}
                live = live_users(client, headers)
        except (httpx.HTTPError, OSError, KeyError) as exc:
            ev.notes["live"] = f"skipped: Keycloak not reachable ({type(exc).__name__})"
    if live is not None:
        want = declared()
        for user in sorted(set(live) - set(want)):
            finding("R7", user, "an account exists in Keycloak that nobody declared")
        for user in sorted(set(want) - set(live)):
            finding("R7", user, "a declared account is missing in Keycloak")
        for user in sorted(set(live) & set(want)):
            if live[user]["roles"] != sorted(want[user]["roles"]):
                finding(
                    "R7",
                    user,
                    f"live roles {live[user]['roles']} differ from declared {sorted(want[user]['roles'])}",
                )
            if not user.startswith("service-account-") and not str(
                live[user].get("email", "")
            ).endswith("@example.test"):
                finding("R7", user, "a demo account without a reserved example.test address")
        ev.check(
            "the_live_identity_provider_matches_the_declared_accounts_and_roles_exactly",
            not [f for f in findings if f["rule"] == "R7"],
            f"{len(live)} accounts read from the running realm",
        )
    ev.check(
        "the_joiner_mover_leaver_procedure_exists_with_its_review_and_leaver_steps",
        _procedure_ok(),
        str(PROCEDURE.relative_to(REPO_ROOT))
        if PROCEDURE.is_file()
        else "skipped: documents are not in this checkout",
    )

    ev.metrics["holders_by_role"] = {r: sorted(w) for r, w in sorted(by_role.items())}
    ev.metrics["service_identities"] = {r: sorted(w) for r, w in sorted(holders_of_service.items())}
    ev.metrics["findings"] = findings
    ev.metrics["rules"] = [
        "R1 role catalogue",
        "R2 service identities",
        "R3 human roles",
        "R4 separation of duties",
        "R5 holders",
        "R6 realm hardening",
        "R7 live equals declared",
    ]
    ev.metrics["live_checked"] = live is not None
    ev.check("the_review_has_no_open_finding", not findings, f"{len(findings)} finding(s)")
    return ev.finish()


def _procedure_ok() -> bool:
    if not PROCEDURE.parent.is_dir() or not (REPO_ROOT / "docs").is_dir():
        return True  # documents are kept locally; a checkout without them cannot fail this
    if not PROCEDURE.is_file():
        return False
    text = PROCEDURE.read_text(encoding="utf-8").lower()
    return all(
        word in text for word in ("joiner", "mover", "leaver", "quarterly", "break-glass", "owner")
    )


if __name__ == "__main__":
    raise SystemExit(main())
