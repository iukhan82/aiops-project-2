#!/usr/bin/env python3
"""P12.05: stable defects are fixed and retested, and what remains open says what it stops the platform doing and who owns it.

    python source-code/acceptance/verify_defects.py

Reads the register in `defects.py` and checks it against the evidence and the matrices:

  * a FIXED defect cites evidence that was regenerated on or after the day of the fix and still passes;
  * an OPEN defect names its impact, its owner and the next action; an ACCEPTED one names its impact and owner;
  * every row of the three matrices (P12.01, P12.02, P12.04) that is partial or a gap is explained by a defect that lists it, and every defect that lists a row lists a row that is partial or a gap;
  * every acceptance target that is not met, only partly met or not yet measured is linked to a defect - and none is left 'not yet measured' at all.

It writes the register as a document (`docs/evidence/DEFECT_REGISTER.md`). Nothing here approves a defect on the assessor's behalf: ACCEPTED means the project has chosen to live with it and said so.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from acceptance import defects as reg  # noqa: E402
from acceptance import matrix as mx  # noqa: E402
from acceptance import matrix_data as data  # noqa: E402
from acceptance import quality  # noqa: E402
from backend.evidence import Evidence  # noqa: E402

TARGETS = REPO_ROOT / "docs" / "requirements" / "ACCEPTANCE_TARGETS.md"
FIXED_ON_OR_AFTER = (
    "2026-09-25"  # the target work and this phase; retest evidence must be at least this new
)


def target_status(result_cell: str) -> str:
    text = result_cell.strip()
    if text.startswith("Not yet measured"):
        return "not measured"
    if "NOT met" in text[:120]:
        return "not met"
    if re.match(r"\*\*(Partly met|Partial)", text):
        return "partial"
    return "measured"


def main() -> int:  # noqa: PLR0915
    ev = Evidence("P12.05", "p12_05_defects", docs_name="p12_05_defects")
    ids = [d.id for d in reg.DEFECTS]
    ev.check(
        "defect_ids_are_unique_and_severities_and_statuses_are_from_the_vocabulary",
        len(ids) == len(set(ids))
        and all(
            d.severity in {"SAFETY", "HIGH", "MEDIUM", "LOW"}
            and d.status in {"FIXED", "OPEN", "ACCEPTED"}
            for d in reg.DEFECTS
        ),
        f"{len(ids)} entries",
    )

    retests = {
        d.id: [mx.evaluate(mx.Row(f"{d.id}#{i}", d.title, (c,))) for i, c in enumerate(d.retest)]
        for d in reg.DEFECTS
        if d.status == "FIXED"
    }
    unretested = [d.id for d in reg.DEFECTS if d.status == "FIXED" and not d.retest]
    ev.check(
        "every_fixed_defect_names_evidence_that_was_regenerated_after_the_fix",
        not unretested,
        f"without retest evidence: {unretested}",
    )
    failing = {
        k: [p for r in rs for p in r.problems]
        for k, rs in retests.items()
        if any(r.verdict == "FAILED" for r in rs)
    }
    ev.check(
        "every_retest_cited_by_a_fixed_defect_exists_and_passes_today",
        not failing,
        str(failing)[:400],
    )
    old = {}
    for k, rs in retests.items():
        for r in rs:
            for f in r.found:
                if f["generated_at"] and f["generated_at"][:10] < FIXED_ON_OR_AFTER:
                    old[f"{k}:{f['file']}"] = f["generated_at"]
    ev.check(
        "every_retest_evidence_is_from_the_fix_or_later_not_from_before_it",
        not old,
        f"older than {FIXED_ON_OR_AFTER}: {old}",
    )
    incomplete = [
        d.id for d in reg.DEFECTS if d.status in ("OPEN", "ACCEPTED") and not (d.impact and d.owner)
    ] + [d.id for d in reg.DEFECTS if d.status == "OPEN" and not d.next_action]
    ev.check(
        "every_open_defect_names_its_impact_owner_and_next_action_and_every_accepted_one_its_impact_and_owner",
        not incomplete,
        f"incomplete: {incomplete}",
    )

    # ---- the matrices' partial and gap rows, and the defects that explain them
    rows = [
        *data.SCENARIOS,
        *data.FAULTS,
        *quality.SAFETY,
        *quality.PRIVACY,
        *quality.ACCESSIBILITY,
        *quality.FAIRNESS,
    ]
    flagged = {r.id: r for r in rows if r.status in ("partial", "gap")}
    # a row whose verdict was changed by what the analysis found (Q-F1) is partial at run time; it is listed here from its own metrics
    flagged.setdefault("Q-F1", next(r for r in quality.FAIRNESS if r.id == "Q-F1"))
    explained = {row: d.id for d in reg.DEFECTS for row in d.rows}
    unexplained = sorted(set(flagged) - set(explained))
    ev.check(
        "every_partial_or_gap_row_of_the_matrices_is_explained_by_a_defect_that_lists_it",
        not unexplained,
        f"unexplained rows: {unexplained}",
    )
    phantom = sorted(set(explained) - set(flagged))
    ev.check(
        "every_row_a_defect_lists_is_a_partial_or_gap_row",
        not phantom,
        f"rows that are not partial or gap: {phantom}",
    )
    named_owner = {rid: re.findall(r"\b([DF]-\d\d)\b", r.owner) for rid, r in flagged.items()}
    mismatched = {
        rid: (named, explained.get(rid))
        for rid, named in named_owner.items()
        if named and explained.get(rid) not in named
    }
    ev.check(
        "the_defect_a_row_names_in_its_owner_is_the_defect_that_lists_the_row",
        not mismatched,
        str(mismatched),
    )

    # ---- the acceptance targets
    statuses: dict[str, str] = {}
    for line in TARGETS.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 5 and re.match(r"^[A-Z]+-\d\d$", cells[1]):
            statuses[cells[1]] = target_status(cells[5])
    not_measured = sorted(k for k, v in statuses.items() if v == "not measured")
    ev.check(
        "no_acceptance_target_is_left_not_yet_measured",
        not not_measured,
        f"{len(statuses)} targets; not yet measured: {not_measured}",
    )
    linked = {link for d in reg.DEFECTS for link in d.links}
    unlinked = sorted(
        k for k, v in statuses.items() if v in ("not met", "partial") and k not in linked
    )
    ev.check(
        "every_acceptance_target_that_is_not_met_or_only_partly_met_is_linked_to_a_defect",
        not unlinked,
        f"targets not met or partial: {sorted(k for k, v in statuses.items() if v in ('not met', 'partial'))}; unlinked {unlinked}",
    )

    counts = Counter(d.status for d in reg.DEFECTS)
    by_severity = Counter((d.status, d.severity) for d in reg.DEFECTS)
    ev.check(
        "the_counts_are_stated",
        True,
        f"{counts['FIXED']} fixed and retested, {counts['OPEN']} open, {counts['ACCEPTED']} accepted",
    )
    ev.metrics = {
        "counts": dict(counts), "open_by_severity": {sev: by_severity[("OPEN", sev)] for sev in ("SAFETY", "HIGH", "MEDIUM", "LOW")}, "fixed_by_severity": {sev: by_severity[("FIXED", sev)] for sev in ("SAFETY", "HIGH", "MEDIUM", "LOW")},
        "targets": statuses, "defects": {d.id: {"title": d.title, "severity": d.severity, "status": d.status, "owner": d.owner, "rows": list(d.rows), "links": list(d.links)} for d in reg.DEFECTS},
    }  # fmt: skip
    ev.notes["what_is_not_claimed"] = (
        "ACCEPTED is the project's own decision to live with a limit; it is not an assessor approval, and none is recorded or implied"
    )

    lines = ["# Defect and finding register (P12.05)", "", "Generated by `source-code/acceptance/verify_defects.py` from `source-code/acceptance/defects.py` and the evidence; do not edit by hand.", "",
             f"**{counts['FIXED']} fixed and retested, {counts['OPEN']} open, {counts['ACCEPTED']} accepted.** Open by severity: " + ", ".join(f"{sev} {by_severity[('OPEN', sev)]}" for sev in ("SAFETY", "HIGH", "MEDIUM", "LOW")) + ".", ""]  # fmt: skip
    for status in ("OPEN", "FIXED", "ACCEPTED"):
        lines += [
            f"## {status.capitalize()}",
            "",
            "| ID | Severity | What | Found by | Impact | Owner | "
            + ("Fix and retest" if status == "FIXED" else "Next action / rows")
            + " |",
            "|---|---|---|---|---|---|---|",
        ]
        for d in (x for x in reg.DEFECTS if x.status == status):
            tail = (
                f"{d.fix} - retest: " + "; ".join(f"`{c.file}`" for c in d.retest)
                if status == "FIXED"
                else (d.next_action + (f" (rows {', '.join(d.rows)})" if d.rows else "")).strip()
            )
            lines.append(
                f"| {d.id} | {d.severity} | {d.title} | {d.found_by} | {d.impact} | {d.owner} | {tail.replace('|', '/')} |"
            )
        lines.append("")
    (REPO_ROOT / "docs" / "evidence" / "DEFECT_REGISTER.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
