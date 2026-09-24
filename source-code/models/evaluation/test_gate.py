"""Ledger that keeps the held-out test split honest.

The test split may be opened for the *final model comparison* exactly once
per (dataset, feature version); any further selection-affecting opening
needs an explicit, recorded reason. Non-selecting uses (runtime parity
replay, latency benchmarks) are allowed but logged, so it is always
auditable who looked at the test data and why.
"""

from __future__ import annotations

import json
from pathlib import Path

LEDGER_PATH = Path(__file__).resolve().parents[1] / "registry" / "test_split_ledger.json"
SELECTING_PURPOSES = {"final_comparison"}


class TestSplitReopened(RuntimeError):  # noqa: N818 - reads better as a noun phrase
    __test__ = False


def _load(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"format": "test-split-ledger/1", "entries": []}


def record_opening(
    purpose: str,
    dataset_sha256: str,
    feature_version: str,
    detail: dict,
    reopen_reason: str | None = None,
    path: Path = LEDGER_PATH,
) -> None:
    ledger = _load(path)
    prior = [
        e
        for e in ledger["entries"]
        if e["purpose"] == purpose
        and e["dataset_sha256"] == dataset_sha256
        and e["feature_version"] == feature_version
    ]
    if purpose in SELECTING_PURPOSES and prior and not reopen_reason:
        raise TestSplitReopened(
            f"test split already opened for {purpose!r} on this dataset/feature version; "
            "pass a reopen_reason to record why it is being opened again"
        )
    entry = {
        "purpose": purpose,
        "dataset_sha256": dataset_sha256,
        "feature_version": feature_version,
        "detail": detail,
    }
    if reopen_reason:
        entry["reopen_reason"] = reopen_reason
    if purpose not in SELECTING_PURPOSES:
        ledger["entries"] = [e for e in ledger["entries"] if e not in prior]
    ledger["entries"].append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
