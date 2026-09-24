"""The demo world: a separate database the operator UI runs against.

The verification scripts write to, and delete from, the `aiops` database, so the
browser stack does not share it. `aiops_demo` is created here from the same
migrations and seeds, holds the real simulated device registry, and is fed by
`feeder.py`. Nothing in it is hand-written: every row is produced by the same
ingestion, detection and correlation code the platform runs.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.loader.platform_loader import register_devices  # noqa: E402
from database.migrate import dsn_from_env, migrate  # noqa: E402
from database.seeds import seed_segments, seed_topology  # noqa: E402

DEFAULT_DATABASE = "aiops_demo"
GEOMETRY = "2026-09-18.1"
DATASET = SOURCE_ROOT / "models" / "intelligence_dataset" / "output" / "run-a"
SENSORS = SOURCE_ROOT / "simulator" / "sensors" / "output" / "run-a"
EMERGENCY = SOURCE_ROOT / "simulator" / "emergency" / "output" / "run-a"


def load_platform_env() -> None:
    env_file = SOURCE_ROOT / "infra" / "platform" / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def use_database(name: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name):
        raise ValueError(f"unsafe database name {name!r}")
    os.environ["POSTGRES_DB"] = name


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _maintenance_dsn() -> str:
    return dsn_from_env().replace(f"dbname={os.environ['POSTGRES_DB']}", "dbname=postgres")


def database_exists(name: str) -> bool:
    with psycopg.connect(_maintenance_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        return cur.fetchone() is not None


def drop_database(name: str) -> None:
    with psycopg.connect(_maintenance_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'
        )  # name validated by use_database


def ensure(name: str = DEFAULT_DATABASE, reset: bool = False) -> dict:
    """Create (or, with reset, recreate) the demo database; migrate; seed topology and segments; register devices."""
    use_database(name)
    if reset and database_exists(name):
        drop_database(name)
    created = not database_exists(name)
    if created:
        with psycopg.connect(_maintenance_dsn(), autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(dsn_from_env()) as conn:
        migrations = migrate(conn)
        topology = seed_topology.seed(conn)
        segments = seed_segments.seed(conn)
        conn.commit()
        devices = (
            jsonl(DATASET / "devices.jsonl")
            + jsonl(SENSORS / "devices.jsonl")
            + jsonl(EMERGENCY / "devices.jsonl")
        )
        registered = register_devices(conn, devices)
    return {
        "database": name,
        "created": created,
        "migrations_applied": len(migrations["applied"]),
        "topology": topology,
        "segments": segments,
        "devices_registered": registered,
    }


if __name__ == "__main__":
    load_platform_env()
    print(
        json.dumps(
            ensure(
                sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATABASE, reset="--reset" in sys.argv
            ),
            indent=2,
            default=str,
        )
    )
