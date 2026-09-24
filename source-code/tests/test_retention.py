"""P05.10: retention pure-logic unit tests. Real-database behavior
(purge-past-window, audit-class exemption, aggregation-before-deletion,
active-control preservation, storage-pressure shortening) needs the real
P05.01/P05.04 Postgres and is proven by
source-code/database/verify_retention.py, not here - see
docs/evidence/p05_10_retention.json.
"""

from database.retention import RETENTION_DAYS, _effective_windows


def test_audit_class_has_no_finite_window() -> None:
    assert RETENTION_DAYS["audit"] is None


def test_finite_classes_are_ordered_short_to_extended() -> None:
    assert RETENTION_DAYS["short"] < RETENTION_DAYS["standard"] < RETENTION_DAYS["extended"]


def test_effective_windows_unchanged_without_pressure() -> None:
    assert _effective_windows(under_pressure=False) == RETENTION_DAYS


def test_effective_windows_shrink_under_pressure_but_audit_stays_unlimited() -> None:
    shrunk = _effective_windows(under_pressure=True)
    assert shrunk["audit"] is None
    assert shrunk["short"] < RETENTION_DAYS["short"]
    assert shrunk["standard"] < RETENTION_DAYS["standard"]
    assert shrunk["extended"] < RETENTION_DAYS["extended"]


def test_effective_windows_never_shrink_to_zero() -> None:
    shrunk = _effective_windows(under_pressure=True)
    assert all(v is None or v >= 1 for v in shrunk.values())
