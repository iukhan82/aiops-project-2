"""Per-request PostgreSQL connections. No pool: at this project's
demonstration scale a connect-per-request is simple and correct; a pooled
connection (psycopg_pool) is the documented upgrade path if P11.06 load
testing shows connection setup cost matters."""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from database.migrate import dsn_from_env  # noqa: E402


def get_conn():
    conn = psycopg.connect(dsn_from_env())
    try:
        yield conn
    finally:
        conn.close()
