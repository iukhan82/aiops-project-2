"""P10.07: the platform correlator - reads alerts and detector anomalies from Prometheus, groups them, keeps the incidents.

    python source-code/backend/aiops/platform_correlator.py --once
    python source-code/backend/aiops/platform_correlator.py --interval 15          # until stopped
    python source-code/backend/aiops/platform_correlator.py --once --no-detector   # alerts only

It connects to PostgreSQL as its own least-privilege role (`svc_platform_correlator`, migration 0029): it can write the
incidents it infers and their history and nothing else, and it cannot record a verified cause. Every incident it writes
carries a hypothesis labelled unverified.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.aiops import platform_signals  # noqa: E402
from backend.aiops.correlation import correlate  # noqa: E402
from backend.repositories import platform_incidents  # noqa: E402
from models.operations_detector.runtime import OperationsDetector  # noqa: E402

PACKAGE = SOURCE_ROOT / "models" / "registry" / "ops-anomaly-detector" / "1.0.0"


def load_detector(package: Path = PACKAGE) -> OperationsDetector | None:
    """The packaged detector, or None (alerts only) when none has been trained here."""
    if not (package / "model.joblib").is_file():
        return None
    return OperationsDetector.load(package)


def cycle(
    conn: psycopg.Connection,
    detector: OperationsDetector | None,
    now: datetime | None = None,
    lookback_s: int = 900,
):
    now = now or datetime.now(UTC)
    signals = platform_signals.collect_live(detector, now, lookback_s)
    drafts = correlate(signals, now)
    return platform_incidents.sync(conn, drafts, now), len(signals), len(drafts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--database", default=os.environ.get("POSTGRES_DB", "aiops"))
    parser.add_argument("--no-detector", action="store_true", help="correlate alerts only")
    args = parser.parse_args()

    from backend.demo import world

    world.load_platform_env()
    os.environ["POSTGRES_DB"] = args.database
    from database.migrate import dsn_from_env

    detector = None if args.no_detector else load_detector()
    print(
        "detector: "
        + (detector.card.get("served_detector", "loaded") if detector else "none (alerts only)"),
        flush=True,
    )
    with psycopg.connect(dsn_from_env(role="svc_platform_correlator")) as conn:
        while True:
            result, signal_count, incident_count = cycle(conn, detector)
            print(
                f"{datetime.now(UTC):%H:%M:%S} signals {signal_count} -> incidents {incident_count}; "
                f"{dict(result) or 'no change'}",
                flush=True,
            )
            if args.once:
                return 0
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
