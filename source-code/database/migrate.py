"""P05.04: ordered, checksummed PostgreSQL migrations.

Each file under migrations/ is numbered (0001_*, 0002_*, ...) and applied at
most once, in filename order, inside its own transaction. A `schema_migrations`
table records the filename, a sha256 checksum of its exact content, and when
it ran. On every subsequent run, an already-applied migration's *current*
on-disk content is re-hashed and compared against the recorded checksum - a
migration hand-edited after being applied fails loudly (ChecksumMismatch)
rather than silently drifting from what actually ran against the database,
matching this project's "a stale or hand-edited artifact fails loudly"
convention (source-code/models/README.md).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


class MigrationError(Exception):
    pass


class ChecksumMismatch(MigrationError):
    """An already-applied migration's on-disk content no longer matches
    what was recorded when it ran - it was hand-edited after the fact."""


@dataclass(frozen=True)
class MigrationFile:
    filename: str
    path: Path
    checksum: str

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def _checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def discover_migrations() -> list[MigrationFile]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return [MigrationFile(f.name, f, _checksum(f.read_text(encoding="utf-8"))) for f in files]


SERVICE_ROLE_SECRETS = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "platform"
    / "output"
    / "service_role_secrets.json"
)


def _service_role_password(role: str) -> str:
    """P09.04 (CTL-12): the password `database/rotate_service_secrets.py` set for a workload-identity role
    (migration 0028), read from the same git-ignored file the rotation script writes. Falls back to the role's
    throwaway creation-time password only when nothing has rotated it yet in this environment - loud, not silent,
    so a deployment that forgot to rotate is visible rather than quietly running on a shared default forever."""
    if SERVICE_ROLE_SECRETS.is_file():
        secrets_by_role = json.loads(SERVICE_ROLE_SECRETS.read_text(encoding="utf-8"))
        if role in secrets_by_role:
            return secrets_by_role[role]
    print(
        f"warning: no rotated password on file for {role!r}; using its migration-time default "
        f"(run python source-code/database/rotate_service_secrets.py)",
        file=sys.stderr,
    )
    return "rotate-me-immediately"


def dsn_from_env(role: str | None = None) -> str:
    """`role`, when given, is one of `rotate_service_secrets.ROLES` - a service connects as itself instead of the
    shared `aiops_app` application role (P09.04, CTL-12), with the password `rotate_service_secrets.py` last set."""
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ["POSTGRES_DB"]
    if role is None:
        user = os.environ["POSTGRES_USER"]
        password = os.environ["POSTGRES_PASSWORD"]
    else:
        user = role
        password = _service_role_password(role)
    return f"host={host} port={port} dbname={db} user={user} password={password}"


def migrate(conn: psycopg.Connection) -> dict:
    """Applies every not-yet-applied migration, in order, each in its own
    transaction. Returns {"applied": [...], "already_applied": [...]}.
    Raises ChecksumMismatch (leaving the DB untouched for that run) if any
    already-applied migration's content has changed on disk."""
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_MIGRATIONS_DDL)
    conn.commit()

    applied_rows: dict[str, str] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT filename, checksum FROM schema_migrations")
        applied_rows = dict(cur.fetchall())

    applied_now: list[str] = []
    already_applied: list[str] = []

    for mig in discover_migrations():
        if mig.filename in applied_rows:
            if applied_rows[mig.filename] != mig.checksum:
                raise ChecksumMismatch(
                    f"{mig.filename} was already applied with checksum "
                    f"{applied_rows[mig.filename]!r} but its current on-disk "
                    f"content hashes to {mig.checksum!r} - it was edited "
                    "after being applied. Fix by adding a new migration, "
                    "never by editing an applied one."
                )
            already_applied.append(mig.filename)
            continue

        with conn.cursor() as cur:
            cur.execute(mig.sql)
            cur.execute(
                "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                (mig.filename, mig.checksum),
            )
        conn.commit()
        applied_now.append(mig.filename)

    return {"applied": applied_now, "already_applied": already_applied}


def main() -> int:
    with psycopg.connect(dsn_from_env()) as conn:
        report = migrate(conn)
    print(f"applied: {report['applied']}")
    print(f"already up to date: {report['already_applied']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
