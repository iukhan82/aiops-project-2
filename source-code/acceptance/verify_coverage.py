#!/usr/bin/env python3
"""P12.07: assessment coverage review - RQ01 to RQ30, each with a status the evidence supports, and the gate result in plain words.

    python source-code/acceptance/verify_coverage.py

For each requirement of `docs/requirements/TRACEABILITY.md` the review states TESTED, IMPLEMENTED, PLANNED or GAP (never DEMONSTRATED: that needs an assessor) and cites what supports it. The verifier
requires that every TESTED and IMPLEMENTED requirement cites evidence that exists and passes today, that every PLANNED one names the task that owns it and every GAP one says what is missing, that the
three matrices' gate results are quoted, and that the open defects behind the statuses are in the register. It rewrites the status and evidence columns of the traceability table, and writes the review
as `docs/evidence/P12_07_COVERAGE_REVIEW.md`.

The gate result is stated as it is: Phase 12's exit gate ('mandatory scenarios, roles and requirements pass or have assessor-approved exceptions') is NOT passed while a scenario step is a gap, a
requirement is a gap, or a requirement is still planned; no exception has been approved by any assessor, and none is claimed.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from acceptance import coverage_data as cov  # noqa: E402
from acceptance import defects as reg  # noqa: E402
from acceptance import matrix as mx  # noqa: E402
from backend.evidence import Evidence  # noqa: E402

TRACE = REPO_ROOT / "docs" / "requirements" / "TRACEABILITY.md"
WORDS = {"TESTED": "Tested", "IMPLEMENTED": "Implemented", "PLANNED": "Planned", "GAP": "Gap"}


def matrix_gate(name: str) -> str:
    doc = json.loads((mx.EVIDENCE / f"{name}.json").read_text(encoding="utf-8"))
    return doc["metrics"]["gate"]


def main() -> int:  # noqa: PLR0915
    ev = Evidence("P12.07", "p12_07_coverage_review", docs_name="p12_07_coverage_review")
    trace = TRACE.read_text(encoding="utf-8").replace("\r\n", "\n")
    documented = {
        m.group(1): m
        for m in re.finditer(r"^\| (RQ\d\d) \| (.*?) \| (.*?) \| (\w+) \|.*$", trace, re.MULTILINE)
    }
    ids = [r.id for r in cov.REQUIREMENTS]
    ev.check(
        "every_requirement_rq01_to_rq30_is_reviewed_once_and_matches_the_traceability_document",
        ids == [f"RQ{i:02d}" for i in range(1, 31)] and set(ids) == set(documented),
        f"{len(ids)} reviewed, {len(documented)} in the document",
    )

    results = []
    for r in cov.REQUIREMENTS:
        row = mx.Row(
            r.id,
            r.note,
            r.cites,
            status="covered" if r.cites else "gap",
            limit=r.note if not r.cites else "",
            owner=", ".join(r.tasks) if not r.cites else "",
        )
        results.append((r, mx.evaluate(row)))
    failed = [
        (r.id, res.problems)
        for r, res in results
        if r.status in ("TESTED", "IMPLEMENTED") and res.verdict == "FAILED"
    ]
    ev.check(
        "every_tested_or_implemented_requirement_cites_evidence_that_exists_and_passes_today",
        not failed and all(r.cites for r, _ in results if r.status in ("TESTED", "IMPLEMENTED")),
        f"failed {failed[:3]}",
    )
    failed_any = [(r.id, res.problems) for r, res in results if res.verdict == "FAILED" and r.cites]
    ev.check(
        "no_citation_of_any_requirement_is_missing_or_failing", not failed_any, f"{failed_any[:3]}"
    )
    unowned = [
        r.id
        for r in cov.REQUIREMENTS
        if r.status in ("PLANNED", "GAP") and not (r.note and r.tasks)
    ]
    ev.check(
        "every_planned_requirement_names_the_task_that_owns_it_and_every_gap_says_what_is_missing_and_who_decides",
        not unowned,
        f"without a note and a task: {unowned}",
    )
    incomplete = [r.id for r in cov.REQUIREMENTS if r.status == "IMPLEMENTED" and not r.note]
    ev.check(
        "every_implemented_requirement_says_what_stops_it_being_tested",
        not incomplete,
        f"without a note: {incomplete}",
    )
    ev.check(
        "demonstrated_is_never_claimed_it_needs_an_assessor",
        all(r.status in WORDS for r in cov.REQUIREMENTS),
        "statuses used: " + ", ".join(sorted({r.status for r in cov.REQUIREMENTS})),
    )
    tasks = (
        set(
            re.findall(
                r"^\| (P\d\d\.\d\d) \|",
                (REPO_ROOT / "TASK_REGISTER.md").read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        )
        if (REPO_ROOT / "TASK_REGISTER.md").exists()
        else set()
    )
    unknown = sorted({t for r in cov.REQUIREMENTS for t in r.tasks} - tasks) if tasks else []
    ev.check(
        "every_task_named_as_an_owner_exists_in_the_register", not unknown, f"unknown: {unknown}"
    )

    gates = {
        name: matrix_gate(name)
        for name in ("p12_01_acceptance_matrix", "p12_02_fault_matrix", "p12_04_quality_matrix")
    }
    open_defects = [d for d in reg.DEFECTS if d.status == "OPEN"]
    counts = Counter(r.status for r in cov.REQUIREMENTS)
    complete = (
        counts["GAP"] == 0
        and counts["PLANNED"] == 0
        and all("NOT PASSED" not in g for g in gates.values())
    )
    gate = (
        ("PASSED" if complete else "NOT PASSED")
        + f": {counts['TESTED']} tested, {counts['IMPLEMENTED']} implemented, {counts['PLANNED']} planned (Phases 13 and 14), {counts['GAP']} gap; matrices: "
        + "; ".join(f"{k.split('_', 2)[2]} {v}" for k, v in gates.items())
        + f"; {len(open_defects)} open defects"
    )
    ev.check(
        "the_gate_result_is_stated_and_quotes_the_three_matrices_and_the_open_defects",
        all("covered" in g for g in gates.values()),
        gate,
    )
    ev.metrics = {
        "gate": gate,
        "status_counts": dict(counts),
        "matrices": gates,
        "open_defects": [d.id for d in open_defects],
        "requirements": {
            r.id: {
                "status": r.status,
                "tasks": list(r.tasks),
                "evidence": [c.file for c in r.cites],
                "note": r.note,
            }
            for r in cov.REQUIREMENTS
        },
    }
    ev.notes["gate"] = gate
    ev.notes["assessor_approval"] = (
        "none has been given and none is claimed: an exception to the exit gate needs the assessor's own approval"
    )

    # ---- the traceability table: status and evidence columns
    lines = trace.split("\n")
    start = next(
        i for i, ln in enumerate(lines) if ln.startswith("| ID | Normalized mandatory requirement")
    )
    end = start + 2
    while end < len(lines) and lines[end].startswith("| RQ"):
        end += 1
    header = "| ID | Normalized mandatory requirement | Planned evidence/tasks | Status | Evidence and limits (P12.07, 2026-09-26) |"
    table = [header, "|---|---|---|---|---|"]
    by_id = {r.id: r for r in cov.REQUIREMENTS}
    for ln in lines[start + 2 : end]:
        cells = [c.strip() for c in ln.strip().strip("|").split(" | ")]
        req = by_id[cells[0]]
        evidence = ", ".join(
            sorted({f"`{c.file.removeprefix('doc:').removeprefix('pytest:')}`" for c in req.cites})[
                :4
            ]
        )
        table.append(
            f"| {cells[0]} | {cells[1]} | {cells[2]} | {WORDS[req.status]} | {req.note} {('Evidence: ' + evidence + '.') if evidence else ''} {('Owner: ' + ', '.join(req.tasks) + '.') if req.tasks and req.status != 'TESTED' else ''} |".replace(
                "  ", " "
            )
        )
    new = [*lines[:start], *table, *lines[end:]]
    text = "\n".join(new)
    section = f"\n## Coverage review (P12.07, 2026-09-26)\n\n**{gate}.**\n\nGenerated by `source-code/acceptance/verify_coverage.py`; the matrices are `docs/evidence/P12_01_ACCEPTANCE_MATRIX.md`, `P12_02_FAULT_MATRIX.md` and `P12_04_QUALITY_MATRIX.md`, the defects `DEFECT_REGISTER.md`, the artifacts `EVIDENCE_INDEX.md`. Status words: Tested (automated evidence passes, limits recorded), Implemented (built, evidenced in part or with material gaps), Planned (Phase 13 or 14), Gap (not met). Demonstrated is never used: it needs an assessor. No assessor has approved any exception.\n"
    text = re.sub(r"\n## Coverage review \(P12\.07.*?(?=\n## |\Z)", "", text, flags=re.DOTALL)
    text = text.replace(
        "\n## Open assessment questions", section + "\n## Open assessment questions", 1
    )
    TRACE.write_text(text, encoding="utf-8", newline="\n")

    review = [
        "# P12.07 assessment coverage review",
        "",
        "Generated by `source-code/acceptance/verify_coverage.py`; do not edit by hand.",
        "",
        f"**{gate}.**",
        "",
        "| RQ | Status | Evidence | Limits / owner |",
        "|---|---|---|---|",
    ]
    for r, res in results:
        cited = "<br>".join(f"`{f['file']}` ({f['matched']})" for f in res.found) or "-"
        review.append(
            f"| {r.id} | {WORDS[r.status]} | {cited} | {r.note} {('Owner: ' + ', '.join(r.tasks)) if r.tasks else ''} |".replace(
                "|", "/", 0
            )
        )
    (REPO_ROOT / "docs" / "evidence" / "P12_07_COVERAGE_REVIEW.md").write_text(
        "\n".join(review) + "\n", encoding="utf-8", newline="\n"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
