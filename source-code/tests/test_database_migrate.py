"""P05.04: migration discovery/checksum unit tests (no database needed).

Real-database behavior (applying migrations, idempotent re-run, constraint
enforcement, checksum-tamper detection, topology seed) needs the real
P05.01 Postgres/PostGIS container and is proven by
source-code/database/verify_migrate.py, not here - see
docs/evidence/p05_04_migrations.json.
"""

from database.migrate import MIGRATIONS_DIR, _checksum, discover_migrations


def test_migrations_are_discovered_in_filename_order() -> None:
    migrations = discover_migrations()
    filenames = [m.filename for m in migrations]
    assert filenames == sorted(filenames)
    assert filenames[0] == "0001_topology.sql"


def test_every_migration_file_is_numbered() -> None:
    for path in MIGRATIONS_DIR.glob("*.sql"):
        prefix = path.name.split("_", 1)[0]
        assert prefix.isdigit() and len(prefix) == 4, path.name


def test_checksum_is_deterministic_and_content_sensitive() -> None:
    assert _checksum("select 1;") == _checksum("select 1;")
    assert _checksum("select 1;") != _checksum("select 2;")


def test_discovered_checksums_match_current_file_content() -> None:
    for mig in discover_migrations():
        assert mig.checksum == _checksum(mig.path.read_text(encoding="utf-8"))
