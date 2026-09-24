"""P09.02 acceptance evidence, against the real running Keycloak:

    python source-code/infra/platform/keycloak/verify_keycloak.py

Two kinds of check. (1) The live realm's configuration read back through the
admin API and compared with the security requirements written out here - not with
the realm file, so drift between the file and the running realm is caught.
(2) Behaviour: the authorization-code flow is attacked the way it would be in
practice (no/plain PKCE challenge, implicit and password grants, an unregistered
redirect, a wrong verifier, a replayed code, a replayed refresh token, an
account being guessed at) and every attack must be refused.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
from pathlib import Path

import httpx

PLATFORM = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLATFORM.parents[1]
sys.path.insert(0, str(SOURCE_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.api import oidc_client as oidc  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from provision import (  # noqa: E402
    BASE,
    IDENTITIES,
    REALM,
    SERVICE_CLIENTS,
    SERVICE_SECRETS,
    admin_token,
    read_env,
)  # noqa: E402

ev = Evidence("P09.02")
ADMIN = f"{BASE}/admin/realms/{REALM}"
BUILT_IN = {"offline_access", "uma_authorization"}
ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]
EXPECTED_ROLES = {
    "operator",
    "supervisor",
    "dispatcher",
    "incident_commander",
    "field_responder",
    "auditor",
    "demo_operator",
    "system:optimization-engine",
    "system:emergency-engine",
    "system:command-executor",
    "system:aiops-remediation",
    "system:outcome-verifier",
}


def refused(response: httpx.Response) -> bool:
    """The authorization endpoint neither showed a login form nor handed back a code or token."""
    location = response.headers.get("location", "")
    handed_out = "code=" in location or "access_token=" in location or "id_token=" in location
    return (
        "kc-form-login" not in response.text
        and not handed_out
        and (response.status_code == 400 or "error=" in location)
    )


def code_for(username: str, password: str) -> tuple[str, str]:
    verifier, challenge = oidc.pkce_pair()
    with httpx.Client(timeout=30) as client:
        page = oidc.authorization_request(client, challenge)
        return oidc.code_from(oidc.submit_credentials(client, page, username, password)), verifier


def login_error(username: str, password: str) -> str:
    verifier, challenge = oidc.pkce_pair()
    with httpx.Client(timeout=30) as client:
        page = oidc.authorization_request(client, challenge)
        posted = oidc.submit_credentials(client, page, username, password)
        match = re.search(r'kc-feedback-text">\s*(.+?)\s*</span>', posted.text, re.DOTALL)
        return f"{posted.status_code}: {match.group(1) if match else '(no message)'}"


def tracked_files() -> list[Path]:
    root = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True, cwd=SOURCE_ROOT
        ).strip()
    )
    names = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
        .decode("utf-8", "replace")
        .split("\0")
    )
    return [root / n for n in names if n]


def main() -> int:  # noqa: PLR0915
    env = read_env()
    identities = json.loads(IDENTITIES.read_text(encoding="utf-8"))
    service_secrets = json.loads(SERVICE_SECRETS.read_text(encoding="utf-8"))
    with httpx.Client(timeout=30) as http:
        headers = {"Authorization": f"Bearer {admin_token(http, env)}"}
        realm = http.get(ADMIN, headers=headers).json()
        clients = {c["clientId"]: c for c in http.get(f"{ADMIN}/clients", headers=headers).json()}
        roles = {
            r["name"]
            for r in http.get(f"{ADMIN}/roles", headers=headers, params={"max": 200}).json()
        }
        ui = clients["aiops-ui"]

        # ---- configuration of the running realm ----
        ev.check(
            "realm_enforces_https_off_localhost_has_no_self_registration_and_locks_out_guessing",
            realm["enabled"]
            and realm["sslRequired"] == "external"
            and not realm["registrationAllowed"]
            and not realm["resetPasswordAllowed"]
            and realm["bruteForceProtected"]
            and realm["failureFactor"] <= 5,
            detail=f"sslRequired={realm['sslRequired']} failureFactor={realm['failureFactor']}",
        )
        ev.check(
            "realm_signs_with_rs256_short_lived_tokens_and_rotates_refresh_tokens",
            realm["defaultSignatureAlgorithm"] == "RS256"
            and realm["accessTokenLifespan"] <= 300
            and realm["revokeRefreshToken"]
            and realm["refreshTokenMaxReuse"] == 0
            and realm["ssoSessionIdleTimeout"] <= 1800
            and realm["ssoSessionMaxLifespan"] <= 28800,
            detail=f"access={realm['accessTokenLifespan']}s idle={realm['ssoSessionIdleTimeout']}s",
        )
        ev.check(
            "realm_has_a_password_policy_and_audit_events_on",
            "length(" in realm["passwordPolicy"]
            and realm["eventsEnabled"]
            and realm["adminEventsEnabled"],
            detail=realm["passwordPolicy"],
        )
        jwks = http.get(f"{oidc.REALM_URL}/protocol/openid-connect/certs").json()["keys"]
        ev.check(
            "published_signing_keys_are_rsa_rs256_only",
            jwks
            and all(
                k["kty"] == "RSA" and k.get("alg") == "RS256" for k in jwks if k.get("use") == "sig"
            ),
            detail=f"{len(jwks)} keys",
        )
        extra_roles = {
            r
            for r in roles
            if r not in EXPECTED_ROLES and r not in BUILT_IN and not r.startswith("default-roles")
        }
        ev.check(
            "the_realm_has_exactly_the_twelve_operating_roles_and_nothing_else_custom",
            roles >= EXPECTED_ROLES and not extra_roles,
            detail=str(sorted(extra_roles)),
        )
        ev.check(
            "ui_client_is_public_pkce_s256_only_no_implicit_no_password_grant_no_service_account_no_secret",
            ui["publicClient"]
            and ui["standardFlowEnabled"]
            and not ui["implicitFlowEnabled"]
            and not ui["directAccessGrantsEnabled"]
            and not ui["serviceAccountsEnabled"]
            and ui["attributes"].get("pkce.code.challenge.method") == "S256"
            and not ui.get("secret"),
            detail=str(ui["attributes"].get("pkce.code.challenge.method")),
        )
        ev.check(
            "ui_client_redirects_and_origins_are_exact_with_no_wildcard_anywhere",
            sorted(ui["redirectUris"]) == sorted(o + "/callback" for o in ORIGINS)
            and sorted(ui["webOrigins"]) == sorted(ORIGINS)
            and sorted(ui["attributes"]["post.logout.redirect.uris"].split("##"))
            == sorted(o + "/login" for o in ORIGINS),
            detail=str(ui["redirectUris"]),
        )
        ev.check(
            "ui_client_token_carries_only_the_users_operating_role_via_scope_mapping_not_full_scope",
            ui["fullScopeAllowed"] is False,
        )
        service_ok = all(
            clients[c]["serviceAccountsEnabled"]
            and not clients[c]["publicClient"]
            and not clients[c]["standardFlowEnabled"]
            and not clients[c]["directAccessGrantsEnabled"]
            and not clients[c]["implicitFlowEnabled"]
            and not clients[c]["fullScopeAllowed"]
            for c in SERVICE_CLIENTS
        )
        ev.check(
            "the_five_service_clients_are_confidential_machine_only_no_browser_flow_no_password_grant",
            service_ok,
        )

        users = http.get(f"{ADMIN}/users", headers=headers, params={"max": 100}).json()
        by_name = {u["username"]: u for u in users}
        mapping_ok, extra = True, []
        for name, spec in identities.items():
            held = {
                r["name"]
                for r in http.get(
                    f"{ADMIN}/users/{by_name[name]['id']}/role-mappings/realm", headers=headers
                ).json()
            }
            mapped = {r for r in held if r not in BUILT_IN and not r.startswith("default-roles")}
            if (
                mapped != {spec["role"]}
                or not by_name[name]["enabled"]
                or by_name[name].get("requiredActions")
            ):
                mapping_ok = False
                extra.append(f"{name}: {sorted(mapped)}")
        ev.check(
            "each_demo_user_holds_exactly_one_operating_role_is_enabled_and_has_no_pending_action",
            mapping_ok,
            detail=str(extra),
        )
        ev.check(
            "sam_and_alex_each_have_a_second_account_so_four_eyes_can_be_demonstrated_with_two_different_people",
            sorted(u for u, s in identities.items() if s["role"] == "supervisor")
            == ["sam.okafor", "sam.two"]
            and sorted(u for u, s in identities.items() if s["role"] == "operator")
            == ["alex.chen", "alex.two"],
        )

        # ---- the authorization-code flow under attack ----
        verifier, challenge = oidc.pkce_pair()
        with httpx.Client(timeout=30) as c:
            ev.check(
                "authorization_request_without_a_pkce_challenge_is_refused",
                refused(oidc.authorization_request(c, None, None)),
            )
        with httpx.Client(timeout=30) as c:
            ev.check(
                "authorization_request_with_the_plain_challenge_method_is_refused",
                refused(oidc.authorization_request(c, challenge, "plain")),
            )
        with httpx.Client(timeout=30) as c:
            ev.check(
                "implicit_flow_response_type_token_is_refused",
                refused(oidc.authorization_request(c, challenge, "S256", response_type="token")),
            )
        with httpx.Client(timeout=30) as c:
            ev.check(
                "hybrid_flow_response_type_code_id_token_is_refused",
                refused(
                    oidc.authorization_request(c, challenge, "S256", response_type="code id_token")
                ),
            )
        with httpx.Client(timeout=30) as c:
            evil = oidc.authorization_request(
                c, challenge, "S256", redirect_uri="http://evil.example/callback"
            )
            ev.check(
                "an_unregistered_redirect_uri_is_refused_and_never_redirected_to",
                evil.status_code == 400 and "evil.example" not in evil.headers.get("location", ""),
                detail=str(evil.status_code),
            )
        with httpx.Client(timeout=30) as c:
            other_path = oidc.authorization_request(
                c, challenge, "S256", redirect_uri="http://localhost:5173/anything-else"
            )
            ev.check(
                "a_registered_origin_with_an_unregistered_path_is_refused",
                other_path.status_code == 400 or refused(other_path),
                detail=str(other_path.status_code),
            )
        with httpx.Client(timeout=30) as c:
            ev.check(
                "a_look_alike_host_on_a_registered_port_is_refused",
                oidc.authorization_request(
                    c, challenge, "S256", redirect_uri="http://localhost.evil.example:5173/callback"
                ).status_code
                == 400,
            )
        password_grant = httpx.post(
            oidc.TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": "aiops-ui",
                "username": "alex.chen",
                "password": identities["alex.chen"]["password"],
            },
            timeout=30,
        )
        ev.check(
            "the_password_grant_is_refused_for_the_ui_client",
            password_grant.status_code in (400, 401) and "access_token" not in password_grant.text,
            detail=password_grant.text[:100],
        )
        machine = httpx.post(
            oidc.TOKEN_URL,
            data={"grant_type": "client_credentials", "client_id": "aiops-ui"},
            timeout=30,
        )
        ev.check(
            "client_credentials_is_refused_for_the_public_ui_client",
            machine.status_code in (400, 401) and "access_token" not in machine.text,
            detail=machine.text[:100],
        )
        no_secret = httpx.post(
            oidc.TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": "system-command-executor",
                "client_secret": "wrong-" + secrets.token_hex(8),
            },
            timeout=30,
        )
        good_secret = httpx.post(
            oidc.TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": "system-command-executor",
                "client_secret": service_secrets["system-command-executor"],
            },
            timeout=30,
        )
        ev.check(
            "a_service_client_needs_its_real_secret",
            no_secret.status_code in (400, 401) and good_secret.status_code == 200,
        )

        code, good_verifier = code_for("alex.chen", identities["alex.chen"]["password"])
        with httpx.Client(timeout=30) as c:
            wrong = oidc.exchange_code(c, code, "x" * 64)
            ev.check(
                "a_valid_code_with_the_wrong_pkce_verifier_is_refused_invalid_grant",
                wrong.status_code == 400 and wrong.json().get("error") == "invalid_grant",
                detail=wrong.text[:100],
            )
            again = oidc.exchange_code(c, code, good_verifier)
            ev.check(
                "after_a_failed_verifier_the_same_code_is_dead_it_cannot_be_retried",
                again.status_code == 400,
                detail=again.text[:100],
            )
        code, good_verifier = code_for("alex.chen", identities["alex.chen"]["password"])
        with httpx.Client(timeout=30) as c:
            first = oidc.exchange_code(c, code, good_verifier)
            replay = oidc.exchange_code(c, code, good_verifier)
        ev.check(
            "an_authorization_code_works_once_and_a_replay_is_refused",
            first.status_code == 200 and replay.status_code == 400,
            detail=f"{first.status_code}/{replay.status_code}",
        )
        after_replay = oidc.refresh(first.json()["refresh_token"])
        ev.check(
            "replaying_a_code_also_revokes_the_tokens_it_had_already_produced",
            after_replay.status_code == 400,
            detail=f"refresh after replay: {after_replay.status_code}",
        )

        session = oidc.login("alex.chen", identities["alex.chen"]["password"])
        rotated = oidc.refresh(session.refresh_token)
        ev.check(
            "refreshing_returns_a_new_refresh_token",
            rotated.status_code == 200 and rotated.json()["refresh_token"] != session.refresh_token,
            detail=str(rotated.status_code),
        )
        reuse = oidc.refresh(session.refresh_token)
        ev.check(
            "the_old_refresh_token_is_refused_after_rotation",
            reuse.status_code == 400 and reuse.json().get("error") == "invalid_grant",
            detail=reuse.text[:100],
        )
        newer = (
            oidc.refresh(rotated.json()["refresh_token"]) if rotated.status_code == 200 else None
        )
        ev.metrics["newer_refresh_token_after_old_one_was_replayed"] = (
            "still valid" if newer is not None and newer.status_code == 200 else "revoked"
        )

        # ---- logout ----
        fresh = oidc.login("ana.petrov", identities["ana.petrov"]["password"])
        end = oidc.end_session(fresh.id_token, fresh.refresh_token)
        ev.check(
            "logout_ends_the_session_and_the_refresh_token_is_dead",
            end.status_code in (200, 204, 302)
            and oidc.refresh(fresh.refresh_token).status_code == 400,
            detail=str(end.status_code),
        )

        # ---- guessing at accounts ----
        real_wrong = login_error("alex.chen", "definitely-not-the-password-1")
        unknown = login_error("nobody.here", "definitely-not-the-password-1")
        ev.check(
            "a_wrong_password_and_an_unknown_user_get_the_same_answer_so_accounts_cannot_be_enumerated",
            real_wrong == unknown and "Invalid" in real_wrong,
            detail=f"{real_wrong!r} vs {unknown!r}",
        )
        throwaway = f"verify-lockout-{secrets.token_hex(3)}"
        made = http.post(
            f"{ADMIN}/users",
            headers=headers,
            json={
                "username": throwaway,
                "enabled": True,
                "emailVerified": True,
                "email": f"{throwaway}@example.test",
                "firstName": "Throw",
                "lastName": "Away",
            },
        )
        user_id = made.headers["Location"].rsplit("/", 1)[1]
        good = secrets.token_urlsafe(18)
        http.put(
            f"{ADMIN}/users/{user_id}/reset-password",
            headers=headers,
            json={"type": "password", "value": good, "temporary": False},
        ).raise_for_status()
        http.post(
            f"{ADMIN}/users/{user_id}/role-mappings/realm",
            headers=headers,
            json=[http.get(f"{ADMIN}/roles/operator", headers=headers).json()],
        ).raise_for_status()
        try:
            before = oidc.login(throwaway, good)
            for _ in range(realm["failureFactor"] + 1):
                try:
                    oidc.login(throwaway, "wrong-" + secrets.token_hex(6))
                except oidc.LoginFailed:
                    pass
            try:
                oidc.login(throwaway, good)
                locked = False
            except oidc.LoginFailed:
                locked = True
            status = http.get(
                f"{ADMIN}/attack-detection/brute-force/users/{user_id}", headers=headers
            ).json()
            ev.check(
                "repeated_wrong_passwords_lock_the_account_even_against_the_correct_password_afterwards",
                bool(before.access_token) and locked and status.get("disabled") is True,
                detail=f"failures={status.get('numFailures')} disabled={status.get('disabled')}",
            )
        finally:
            http.delete(f"{ADMIN}/users/{user_id}", headers=headers)

        # ---- no secrets in the repository ----
        secrets_to_find = {
            "admin password": env["KEYCLOAK_ADMIN_PASSWORD"],
            **{f"{u} password": s["password"] for u, s in identities.items()},
            **{f"{c} secret": v for c, v in service_secrets.items()},
        }
        leaks = []
        for path in tracked_files():
            if not path.is_file() or path.stat().st_size > 5_000_000:
                continue
            blob = path.read_bytes()
            leaks += [
                f"{label} in {path.name}"
                for label, value in secrets_to_find.items()
                if value and value.encode() in blob
            ]
        ev.check(
            "no_generated_password_admin_password_or_client_secret_appears_in_any_tracked_file",
            not leaks,
            detail=str(leaks[:3]),
        )
        ignored = all(
            subprocess.run(
                ["git", "check-ignore", "-q", str(f)], cwd=SOURCE_ROOT, check=False
            ).returncode
            == 0
            for f in (IDENTITIES, SERVICE_SECRETS, PLATFORM / ".env")
        )
        ev.check("the_files_holding_those_secrets_are_git_ignored", ignored)
        realm_file = json.loads(
            (Path(__file__).parent / "realm" / "aiops-realm.json").read_text(encoding="utf-8")
        )
        ev.check(
            "the_committed_realm_file_holds_no_client_secret_and_no_user_but_credential_less_service_accounts",
            not any("secret" in c for c in realm_file["clients"])
            and all(
                u.get("serviceAccountClientId") and "credentials" not in u
                for u in realm_file.get("users", [])
            ),
        )
        ev.metrics["realm"] = {
            "access_token_lifespan_s": realm["accessTokenLifespan"],
            "sso_idle_s": realm["ssoSessionIdleTimeout"],
            "sso_max_s": realm["ssoSessionMaxLifespan"],
            "brute_force_failure_factor": realm["failureFactor"],
            "roles": len(EXPECTED_ROLES),
            "demo_users": len(identities),
            "service_clients": len(SERVICE_CLIENTS),
        }
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
