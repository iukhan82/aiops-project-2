#!/usr/bin/env python3
"""P13.04: the required runbooks exist, are indexed, and the numbers the backup/rollback/recovery runbook quotes still match the real evidence
they were taken from (so a later re-run of that evidence with different timings does not leave the runbook quietly wrong), and the honesty
markers about what has and has not been exercised are still there.

    python source-code/scripts/verify_runbooks.py

Writes `docs/evidence/p13_04_runbooks.json`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402

RUNBOOKS = REPO_ROOT / "docs" / "runbooks"
REQUIRED_FILES = (
    RUNBOOKS / "README.md",
    RUNBOOKS / "TARGET_DEPLOYMENT_RUNBOOK.md",
    RUNBOOKS / "BACKUP_ROLLBACK_RECOVERY_RUNBOOK.md",
    RUNBOOKS / "INCIDENT_RESPONSE_AND_BREACH_RUNBOOK.md",
    REPO_ROOT / "docs" / "security" / "ACCESS_REVIEW.md",
)

# (procedure, evidence file, JSON path into it, the number of decimal places the runbook rounds to) - every one must appear in the runbook text.
QUOTED_NUMBERS = (
    ("recovery point", ["disaster", "recovery_point_seconds_age_of_backup"], 1),
    ("restore time", ["disaster", "restore_seconds"], 2),
    ("recovery time", ["disaster", "seconds_from_loss_of_the_volume_to_a_passing_end_to_end_check"], 1),
    ("backup time", ["backup", "seconds"], 2),
    ("undo time", ["rollout", "api_undo_seconds"], 1),
    ("recreate outage", ["rollout", "executor_outage_seconds"], 1),
)  # fmt: skip

REQUIRED_HONESTY_MARKERS = (
    (RUNBOOKS / "BACKUP_ROLLBACK_RECOVERY_RUNBOOK.md", "scheduled"),
    (RUNBOOKS / "BACKUP_ROLLBACK_RECOVERY_RUNBOOK.md", "D-03"),
    (RUNBOOKS / "INCIDENT_RESPONSE_AND_BREACH_RUNBOOK.md", "NOT live-drilled"),
    (RUNBOOKS / "INCIDENT_RESPONSE_AND_BREACH_RUNBOOK.md", "not yet true"),
)


def dig(doc: dict, path: list[str]) -> float:
    for key in path:
        doc = doc[key]
    return doc


def main() -> int:
    ev = Evidence("P13.04", "p13_04_runbooks", docs_name="p13_04_runbooks")

    missing = [str(p.relative_to(REPO_ROOT)) for p in REQUIRED_FILES if not p.is_file()]
    ev.check("every_required_runbook_file_exists", not missing, str(missing))

    recovery = json.loads(
        (REPO_ROOT / "docs" / "evidence" / "p11_05_target_recovery.json").read_text(
            encoding="utf-8"
        )
    )["metrics"]
    text = (RUNBOOKS / "BACKUP_ROLLBACK_RECOVERY_RUNBOOK.md").read_text(encoding="utf-8")
    stale = []
    for label, path, decimals in QUOTED_NUMBERS:
        value = dig(recovery, path)
        rendered = f"{value:.{decimals}f}"
        if rendered not in text:
            stale.append((label, rendered))
    ev.check(
        "the_backup_rollback_recovery_runbook_quotes_the_real_p11_05_numbers",
        not stale,
        f"not found as written, re-check the runbook against docs/evidence/p11_05_target_recovery.json: {stale}",
    )

    restarts = recovery.get("restarts", {})
    missing_components = [
        name for name in restarts if name not in text and name.removeprefix("aiops-") not in text
    ]
    ev.check(
        "every_restarted_component_p11_05_measured_is_named_in_the_runbook_table",
        not missing_components,
        f"measured but not named: {missing_components}",
    )

    honesty_missing = [
        f"{p.name}: {marker}"
        for p, marker in REQUIRED_HONESTY_MARKERS
        if marker not in p.read_text(encoding="utf-8")
    ]
    ev.check(
        "the_runbooks_still_state_what_has_and_has_not_been_exercised",
        not honesty_missing,
        str(honesty_missing),
    )

    index = (RUNBOOKS / "README.md").read_text(encoding="utf-8")
    for required in ("incident", "rollback", "backup", "access", "breach", "recovery"):
        ev.check(
            f"the_index_covers_{required}",
            required in index.lower(),
            "docs/runbooks/README.md",
        )

    ev.metrics = {
        "required_files": len(REQUIRED_FILES),
        "quoted_numbers_checked": len(QUOTED_NUMBERS),
    }
    ev.notes["what_this_does_not_check"] = (
        "that a person can actually follow these procedures under pressure - only that the required documents exist, cite real numbers accurately, and disclose what is not yet exercised"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
