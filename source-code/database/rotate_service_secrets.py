"""P09.04 (CTL-14): rotate the Postgres passwords of the three workload-identity roles migration 0028 creates.

    python source-code/database/rotate_service_secrets.py                 # rotate all three
    python source-code/database/rotate_service_secrets.py --role svc_outcome_verifier   # rotate just one

Roles are cluster-level (shared by every database on the server), so `ALTER ROLE ... PASSWORD` runs once per role, not
once per database - unlike the migration that created them. The new password is written to the same git-ignored
`infra/platform/output/` directory the realm provisioning script already uses for the Keycloak service-client secrets,
in the same shape, so both are one place an operator looks. Existing connections are unaffected until they reconnect
(psycopg's own reconnect-on-drop path, already proven for the executor/verifier/feeder against a real Postgres
restart, picks up the new password on its next connection attempt): this script does not, and should not, kill a live
session to force an immediate cutover - the caller decides whether to restart a service to pick up the new password
sooner, and can always run the old and new password side by side for exactly as long as it takes to redeploy.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

import psycopg
from psycopg import sql

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from database.migrate import dsn_from_env  # noqa: E402

OUTPUT_DIR = SOURCE_ROOT / "infra" / "platform" / "output"
SECRETS_PATH = OUTPUT_DIR / "service_role_secrets.json"
ROLES = (
    "svc_command_executor",
    "svc_outcome_verifier",
    "svc_scenario_control",
    "svc_platform_correlator",
    "svc_remediation_worker",
)


def record(role: str, password: str) -> None:
    """Persist one role's password in the git-ignored secrets file: a temp file in the same directory, then an atomic
    replace, so a crash never leaves a half-written file that would lock every service out."""
    existing = (
        json.loads(SECRETS_PATH.read_text(encoding="utf-8")) if SECRETS_PATH.is_file() else {}
    )
    existing[role] = password
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SECRETS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, SECRETS_PATH)


def rotate(conn: psycopg.Connection, role: str) -> str:
    """Changes the role's password in Postgres AND records it in the secrets file, in this one function: a caller cannot
    rotate the database side and forget the file, which would leave every service unable to connect as that role.

    `ALTER ROLE ... PASSWORD` takes a literal, not a bind parameter (it is not valid in that position in Postgres's
    grammar) - `sql.Literal` still escapes it safely, the same protection a bound parameter would give."""
    password = secrets.token_urlsafe(32)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password))
        )
    conn.commit()
    record(role, password)
    return password


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--role", choices=ROLES, action="append", dest="roles")
    args = parser.parse_args()
    targets = args.roles or list(ROLES)

    with psycopg.connect(dsn_from_env()) as conn:
        for role in targets:
            rotate(conn, role)
            print(f"rotated {role}", flush=True)

    print(f">> wrote {SECRETS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
