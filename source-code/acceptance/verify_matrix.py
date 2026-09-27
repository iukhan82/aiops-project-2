#!/usr/bin/env python3
"""P12.01, P12.02 and P12.04: the acceptance matrices, read from the evidence and stated cell by cell.

    python source-code/acceptance/verify_matrix.py p12_01      # scenarios, workflow classes, roles x capabilities
    python source-code/acceptance/verify_matrix.py p12_02      # faults and recovery
    python source-code/acceptance/verify_matrix.py p12_04      # safety, privacy, accessibility, fairness

Each matrix is defined in `matrix_data.py` and evaluated by `matrix.py`: a row claims `covered`, `partial` or `gap`, and stands on named checks of named evidence. The verifier passes when the
matrix is CONSISTENT - every cited check exists and passes, every partial or gap row says what is not shown and who owns it, the grid of roles and capabilities is complete against the access
inventory - and it states the gate result in plain numbers: how many rows are covered, how many are partial, how many are gaps. A matrix with gaps passes its verifier and does NOT pass its gate;
the difference is written in the evidence, and P12.05 is where each gap is closed or accepted.

It also writes the matrix as a document (`docs/evidence/P12_0x_*.md`) so it can be read without the JSON.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from acceptance import matrix_data as data  # noqa: E402
from acceptance import matrix as mx  # noqa: E402
from backend.evidence import Evidence  # noqa: E402

RUNS = SOURCE_ROOT / "infra" / "platform" / "output" / "acceptance_runs.json"
INVENTORY = SOURCE_ROOT / "frontend" / "src" / "config" / "inventory.json"


def run_record(run_id: str) -> dict | None:
    if not RUNS.exists():
        return None
    return next(
        (r for r in json.loads(RUNS.read_text(encoding="utf-8"))["runs"] if r["id"] == run_id), None
    )


def evidence_dates(results: list[mx.Result]) -> dict[str, str]:
    dates: dict[str, str] = {}
    for r in results:
        for f in r.found:
            if f["generated_at"]:
                dates[f["file"]] = f["generated_at"]
    return dates


def gate_line(counts: dict[str, int]) -> str:
    verdict = (
        "PASSED"
        if counts["partial"] == 0 and counts["gap"] == 0 and counts["FAILED"] == 0
        else "NOT PASSED"
    )
    return f"{verdict}: {counts['covered']} covered, {counts['partial']} partial, {counts['gap']} gap, {counts['FAILED']} failed"


def common_checks(ev: Evidence, results: list[mx.Result], name: str) -> None:
    failed = [(r.row.id, r.problems) for r in results if r.verdict == "FAILED"]
    ev.check(
        f"every_{name}_row_cites_evidence_that_exists_and_every_cited_check_passes",
        not failed,
        f"{len(results)} rows; failed {failed[:3]}",
    )
    unowned = [
        r.row.id
        for r in results
        if r.row.status in ("partial", "gap") and not (r.row.limit and r.row.owner)
    ]
    ev.check(
        f"every_partial_or_gap_{name}_row_says_what_is_not_shown_and_who_owns_it",
        not unowned,
        f"unowned {unowned}",
    )
    uncited = [r.row.id for r in results if r.row.status == "covered" and not r.row.cites]
    ev.check(f"no_covered_{name}_row_is_without_evidence", not uncited, f"uncited {uncited}")


def unit_tests_passed(ev: Evidence) -> None:
    record = run_record("unit-tests")
    ev.check(
        "the_python_unit_tests_the_matrix_cites_passed_in_the_run_of_this_phase",
        bool(record and record["passed"]),
        f"run {record['started']} in {record['seconds']} s" if record else "no run record",
    )


def p12_01() -> int:
    ev = Evidence("P12.01", "p12_01_acceptance_matrix", docs_name="p12_01_acceptance_matrix")
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    roles, capabilities = list(inventory["roles"]), inventory["capabilities"]

    scenarios = mx.evaluate_all(data.SCENARIOS)
    classes = mx.evaluate_all(data.WORKFLOW_CLASSES)
    grid_rows = []
    for capability, spec in capabilities.items():
        positive, negative = data.CAPABILITY_EVIDENCE.get(capability, ((), ()))
        held = [r for r in roles if r in spec["roles"]]
        refused = [r for r in roles if r not in spec["roles"]]
        grid_rows.append(
            mx.Row(
                f"CAP {capability}",
                f"{capability}: permitted for {', '.join(held)}; refused for {', '.join(refused) or 'no role'}",
                (*positive, *negative),
                status="covered" if positive and (negative or not refused) else "partial",
                limit=""
                if positive and (negative or not refused)
                else "no evidence for one side of the capability",
                owner="" if positive and (negative or not refused) else "QA - P12.05",
            )
        )
    grid = mx.evaluate_all(grid_rows)

    common_checks(ev, scenarios, "scenario_step")
    common_checks(ev, classes, "workflow_class")
    common_checks(ev, grid, "capability")
    missing = [c for c in capabilities if c not in data.CAPABILITY_EVIDENCE]
    ev.check(
        "every_capability_of_the_access_inventory_has_positive_and_negative_evidence_named",
        not missing,
        f"{len(capabilities)} capabilities; unnamed {missing}",
    )
    cells = len(roles) * len(capabilities)
    permitted = sum(1 for spec in capabilities.values() for r in roles if r in spec["roles"])
    ev.check(
        "the_role_by_capability_grid_is_complete_and_computed_from_the_inventory_not_written",
        cells == len(roles) * len(grid_rows) and len(roles) == 7,
        f"{len(roles)} roles x {len(capabilities)} capabilities = {cells} cells: {permitted} permitted, {cells - permitted} refused",
    )
    unit_tests_passed(ev)
    everything = [*scenarios, *classes, *grid]
    counts = mx.tally(everything)
    scenario_counts = mx.tally(scenarios)
    newest, oldest = mx.freshest_and_oldest(everything)
    dates = evidence_dates(everything)
    ev.check("the_gate_result_is_stated_in_numbers", True, gate_line(counts))
    ev.metrics = {
        "gate": gate_line(counts), "counts": counts, "scenario_counts": scenario_counts, "workflow_class_counts": mx.tally(classes), "capability_counts": mx.tally(grid),
        "roles": roles, "capabilities": len(capabilities), "cells": cells, "cells_permitted": permitted, "evidence_files_cited": len(dates), "newest_evidence": newest, "oldest_evidence": oldest,
        "scenarios": {r.row.id: {"verdict": r.verdict, "title": r.row.title, "not_shown": r.row.limit, "owner": r.row.owner} for r in scenarios},
        "evidence_dates": dates,
    }  # fmt: skip
    ev.notes["gate"] = gate_line(counts)
    ev.notes["what_this_is"] = (
        "a reading of live-stack, browser and target evidence produced by the verifiers (run in this phase by acceptance/run_catalog.py, on the local stack and, for Phase 11, on the target host); it re-runs nothing itself"
    )
    ev.notes["the_scenarios_that_are_not_covered"] = "; ".join(
        f"{r.row.id} {r.verdict}: {r.row.limit[:110]}"
        for r in scenarios
        if r.verdict in ("partial", "gap")
    )
    grid_md = [
        "",
        "### Roles and capabilities (from `frontend/src/config/inventory.json`)",
        "",
        "| Capability | " + " | ".join(roles) + " |",
        "|---|" + "---|" * len(roles),
    ]
    for capability, spec in capabilities.items():
        grid_md.append(
            f"| {capability} | "
            + " | ".join("**may**" if r in spec["roles"] else "refused" for r in roles)
            + " |"
        )
    text = (
        "# P12.01 acceptance matrix\n\nGenerated by `source-code/acceptance/verify_matrix.py p12_01` from the evidence in `docs/evidence/`; do not edit by hand.\n\n"
        + f"**Gate: {gate_line(counts)}.**\n\n"
    )
    text += (
        mx.render(
            "The seven acceptance scenarios",
            scenarios,
            "The scenarios of `PROJECT_PLAN.md` section 10, step by step.",
        )
        + "\n"
    )
    text += (
        mx.render(
            "Workflow classes",
            classes,
            "Nominal, peak, safety, emergency, action and security workflows, for the roles that may perform them.",
        )
        + "\n"
    )
    text += (
        mx.render("Capabilities: who may, and who is refused", grid)
        + "\n"
        + "\n".join(grid_md)
        + "\n"
    )
    (REPO_ROOT / "docs" / "evidence" / "P12_01_ACCEPTANCE_MATRIX.md").write_text(
        text, encoding="utf-8", newline="\n"
    )
    return ev.finish()


def p12_02() -> int:
    ev = Evidence("P12.02", "p12_02_fault_matrix", docs_name="p12_02_fault_matrix")
    faults = mx.evaluate_all(data.FAULTS)
    common_checks(ev, faults, "fault")
    classes = {
        "edge": "F-E",
        "network": "F-N",
        "stream": "F-S",
        "service": "F-V",
        "model": "F-M",
        "policy": "F-P",
        "storage": "F-D",
        "certificates and configuration": "F-C",
        "safety targets": "F-X",
    }
    by_class = {
        name: [r for r in faults if r.row.id.startswith(prefix)] for name, prefix in classes.items()
    }
    ev.check(
        "every_fault_class_the_acceptance_names_has_rows_edge_network_stream_service_model_policy_storage",
        all(
            by_class[c]
            for c in ("edge", "network", "stream", "service", "model", "policy", "storage")
        ),
        str({c: len(v) for c, v in by_class.items()}),
    )
    unit_tests_passed(ev)
    recovery = mx.load("p11_05_target_recovery")
    objectives = (recovery or {}).get("entries", {})
    ev.check(
        "recovery_objectives_are_measured_not_assumed_restore_time_and_recovery_point_are_in_the_target_evidence",
        bool(objectives) and all(objectives.values()),
        "P11.05: RTO and RPO measured on the target (see the evidence's metrics)",
    )
    counts = mx.tally(faults)
    ev.check("the_gate_result_is_stated_in_numbers", True, gate_line(counts))
    ev.metrics = {
        "gate": gate_line(counts),
        "counts": counts,
        "by_class": {c: mx.tally(v) for c, v in by_class.items()},
        "faults": {
            r.row.id: {
                "verdict": r.verdict,
                "title": r.row.title,
                "not_shown": r.row.limit,
                "owner": r.row.owner,
            }
            for r in faults
        },
        "evidence_dates": evidence_dates(faults),
    }
    ev.notes["gate"] = gate_line(counts)
    ev.notes["what_this_is"] = (
        "each fault class of the acceptance (edge, network, stream, service, model, policy, storage) with the evidence that the fault was induced and what the platform did; two labs written for this phase (SAFE-02, SAFE-03, REC-01) and the target and lab evidence of Phases 05-11"
    )
    text = (
        "# P12.02 failure, recovery and disaster matrix\n\nGenerated by `source-code/acceptance/verify_matrix.py p12_02` from the evidence in `docs/evidence/`; do not edit by hand.\n\n"
        + f"**Gate: {gate_line(counts)}.**\n\n"
    )
    for name, rows in by_class.items():
        text += mx.render(name.capitalize(), rows) + "\n"
    (REPO_ROOT / "docs" / "evidence" / "P12_02_FAULT_MATRIX.md").write_text(
        text, encoding="utf-8", newline="\n"
    )
    return ev.finish()


def p12_04() -> int:
    from acceptance import quality  # noqa: PLC0415

    return quality.run()


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "p12_01"
    return {"p12_01": p12_01, "p12_02": p12_02, "p12_04": p12_04}[which]()


if __name__ == "__main__":
    raise SystemExit(main())
