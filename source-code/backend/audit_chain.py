"""P09.05 (CTL-15): independent verification of the hash chain migration 0026 adds to eight histories: four that never
allow DELETE (`operator_audit`, `scenario_control_audit`, `policy_decisions`, `retention_runs`) and four that allow it only
as a CASCADE from a retention purge of their parent row (`incident_transitions`, `command_transitions`,
`emergency_call_transitions`, `emergency_assignment_transitions` - CTL-18, `database/retention.py`). `retention_runs`
records every purge's `commands_deleted`/`incidents_deleted` counts and `run_at`, so it is what turns an unexplained gap
in the four cascade-eligible tables into an accounted-for one.

    python source-code/backend/audit_chain.py [--database aiops_demo]

This is a FORENSIC check, not an enforcement mechanism - it does not rely on the append-only trigger having run, so it is
exactly what still catches a database superuser who dropped the trigger, rewrote a row, and put the trigger back: for every
row that still exists, its stored hash is recomputed from its own content and must match - a rewrite that skipped this
would be caught here regardless of whether the trigger ran.

Two writers committing at the same instant can legitimately link to the same `prev_hash` (see the migration), so the chain
is a tree, not a strict list - accepted, not flagged. A `prev_hash` that names no surviving row is different: for the three
DELETE-refused tables it can only mean tampering (rewrite-then-splice, or a deletion that should have been impossible) and
is reported as broken. For the four retention-eligible tables it is EXPECTED after a purge and is reported separately, as
gaps, not failures - the guarantee those four tables keep is "no surviving row was rewritten," not "nothing was ever
deleted," which retention makes untrue by design.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from database.migrate import dsn_from_env  # noqa: E402

# table -> (order column, DELETE is ever legitimate for this table)
CHAINED_TABLES: dict[str, tuple[str, bool]] = {
    "operator_audit": ("audit_id", False),
    "scenario_control_audit": ("id", False),
    "policy_decisions": ("chain_seq", False),
    "retention_runs": ("id", False),
    "incident_transitions": ("id", True),
    "command_transitions": ("id", True),
    "emergency_call_transitions": ("id", True),
    "emergency_assignment_transitions": ("id", True),
}

# The hash the trigger stored was computed over `row_to_json(NEW)::text` at the moment `row_hash` was still NULL - and
# `row_to_json` is NOT uniformly formatted: it renders the row's own top-level fields compactly (no space after `:`
# or `,`) but inlines a nested jsonb column (`detail`, `input`) using JSONB'S OWN canonical text form, which DOES put a
# space after `:` and `,`. Re-serializing the row from a client library (even byte-for-byte reproducing Postgres's
# json.dumps-equivalent rules) has to replicate that mix correctly for every column type, which is fragile and was
# wrong the first time this was tried here. Recomputing the hash in the SAME engine that computed it the first time -
# one query, `row_to_json(t)` on the row exactly as it is now, with the trailing `"row_hash":"<hex>"` regenerated back
# to `"row_hash":null` by text substitution (row_hash is always the LAST column: every one of these tables got it by
# `ALTER TABLE ... ADD COLUMN`, which always appends) - sidesteps the whole formatting question entirely.
VERIFY_SQL_TEMPLATE = (
    "SELECT {order_col}, prev_hash, row_hash, "
    "encode(digest(prev_hash || '|' || regexp_replace(row_to_json(t)::text, "
    "',\"row_hash\":\"[0-9a-f]+\"}}$', ',\"row_hash\":null}}'), 'sha256'), 'hex') = row_hash AS hash_ok "
    "FROM {table} t WHERE row_hash IS NOT NULL ORDER BY {order_col}"
)


def verify_table(conn: psycopg.Connection, table: str, order_col: str, deletable: bool) -> dict:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE row_hash IS NULL")  # noqa: S608 - table from CHAINED_TABLES, not input
        (legacy_count,) = cur.fetchone()
        # A row from before migration 0026 (or before this table joined the chain) has NULL prev_hash/row_hash: the
        # trigger only computes one on INSERT, and never touches an existing row. That is expected, not tampering -
        # the guarantee only covers what has been inserted since. The first genuinely chained row still correctly
        # links to 'genesis:<table>': the trigger reads the newest row's row_hash to find its own prev_hash, and a
        # legacy row's is NULL, so COALESCE falls back to genesis exactly as if the table had been empty.
        cur.execute(VERIFY_SQL_TEMPLATE.format(order_col=order_col, table=table))  # noqa: S608 - table/col from CHAINED_TABLES, not input
        rows = cur.fetchall()
    known_hashes = {f"genesis:{table}"}
    unresolved_links: list[int] = []
    hash_mismatches: list[int] = []
    for seq, prev_hash, row_hash, hash_ok in rows:
        if prev_hash not in known_hashes:
            unresolved_links.append(seq)
        if not hash_ok:
            hash_mismatches.append(seq)
        known_hashes.add(row_hash)
    # A row rewritten in place and then spliced back with a fabricated prev_hash produces a hash mismatch on THAT row
    # regardless of whether it also breaks a link, so hash_mismatches alone is decisive; unresolved_links is either
    # certain tampering (not deletable) or an expected retention gap (deletable) - never both at once for one table.
    broken_links = [] if deletable else unresolved_links
    gaps = unresolved_links if deletable else []
    return {
        "table": table,
        "rows": len(rows),
        "legacy_rows": legacy_count,
        "broken_links": broken_links,
        "gaps": gaps,
        "hash_mismatches": hash_mismatches,
        "intact": not broken_links and not hash_mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database", default=None)
    args = parser.parse_args()
    if args.database:
        os.environ["POSTGRES_DB"] = args.database
    all_intact = True
    with psycopg.connect(dsn_from_env()) as conn:
        for table, (order_col, deletable) in CHAINED_TABLES.items():
            result = verify_table(conn, table, order_col, deletable)
            all_intact &= result["intact"]
            status = "intact" if result["intact"] else "BROKEN"
            legacy = (
                f" ({result['legacy_rows']} from before the chain, not checked)"
                if result["legacy_rows"]
                else ""
            )
            print(f"{table}: {result['rows']} chained rows{legacy}, chain {status}", flush=True)
            for seq in result["broken_links"]:
                print(
                    f"  {table}[{order_col}={seq}]: prev_hash names no row that exists", flush=True
                )
            for seq in result["gaps"]:
                print(
                    f"  {table}[{order_col}={seq}]: prev_hash names a row this table no longer has (expected if retention purged it)",
                    flush=True,
                )
            for seq in result["hash_mismatches"]:
                print(
                    f"  {table}[{order_col}={seq}]: stored hash does not match its content",
                    flush=True,
                )
    print("every chained history is intact" if all_intact else "TAMPERING DETECTED")
    return 0 if all_intact else 1


if __name__ == "__main__":
    raise SystemExit(main())
