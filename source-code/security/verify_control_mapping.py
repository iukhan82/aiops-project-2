"""P09.07 acceptance evidence: the framework mapping is complete, honest and consistent with the control catalogue and the register.

    python source-code/security/verify_control_mapping.py            # verify, write evidence
    python source-code/security/verify_control_mapping.py --render   # also rewrite the generated tables in docs/security/CONTROL_MAPPING.md

`security/framework_mapping.py` holds the mapping of the platform's controls (`controls.json`) and documents to items of ISO/IEC 27001
Annex A, NIST CSF 2.0, ETSI EN 303 645, IEC 62443, OWASP ASVS and API Top 10, NIST AI RMF, ISO/IEC 42001 and GDPR reference points. What is
checked here, and against what:

* the JSON on disk is what the Python source produces, and ISO/IEC 27001 Annex A is complete (all 93 controls, none twice);
* every cited control exists in the catalogue and every control is cited by at least one framework item (nothing is left unmapped);
* coverage is COMPUTED from the controls' real status (`evidenced` only when every cited control is `implemented`), never written;
* every applicable item that is not fully evidenced names a task that will close it (a real, not-yet-done task) or a written
  reason it is accepted for a prototype; every not-applicable item says why;
* every owner is a real role code, every cited document exists, every named next task exists in the register;
* no text claims compliance, conformity or certification (the disclaimer that says so is the one place the words appear);
* the generated tables in `docs/security/CONTROL_MAPPING.md` are the ones the data produces.

Nothing here assesses the platform against a standard. It shows where evidence exists, where it stops, and who owns the gap.
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

from backend.evidence import Evidence  # noqa: E402
from security import framework_mapping as fm  # noqa: E402

CONTROLS = SOURCE_ROOT / "security" / "controls.json"
REGISTER = REPO_ROOT / "TASK_REGISTER.md"
DOC = REPO_ROOT / "docs" / "security" / "CONTROL_MAPPING.md"
BEGIN, END = "<!-- BEGIN GENERATED: {name} -->", "<!-- END GENERATED: {name} -->"
OWNERS = {
    "LEAD",
    "ARCH",
    "SIM",
    "EDGE",
    "DATA",
    "BACKEND",
    "EMERG",
    "CONTROL",
    "UX",
    "UI",
    "OPS",
    "SEC",
    "GRC",
    "DEVOPS",
    "QA",
    "PRESENT",
}
APPLICABILITY = {"applies", "partly", "not_applicable"}
CLAIM_WORDS = re.compile(
    r"\b(compliant|compliance|certified|certification|conformant|conformity|conforms|complies|meets the requirements)\b",
    re.I,
)
NEGATION = re.compile(r"\b(no|not|never|without|nor|neither|none)\b", re.I)
ISO_EXPECTED = (
    [f"5.{i}" for i in range(1, 38)]
    + [f"6.{i}" for i in range(1, 9)]
    + [f"7.{i}" for i in range(1, 15)]
    + [f"8.{i}" for i in range(1, 35)]
)
ev = Evidence("P09.07", "p09_07_control_mapping", docs_name="p09_07_control_mapping")


def coverage(item: dict, controls: dict[str, dict]) -> str:
    if item["applicability"] == "not_applicable":
        return "out_of_scope"
    states = [controls[c]["status"] for c in item["controls"]]
    if states and all(s == "implemented" for s in states):
        return "evidenced"
    if any(s in ("implemented", "partial") for s in states):
        return "partial"
    if states:
        return "gap"
    return "documented" if item["docs"] else "gap"


def register_tasks() -> dict[str, str]:
    """task id -> status, from the register (empty when the documents are not in this checkout)."""
    if not REGISTER.is_file():
        return {}
    return {
        m.group(1): m.group(2)
        for m in re.finditer(
            r"^\| (P\d\d\.\d\d) \|.*?\|\s*(TODO|IN_PROGRESS|IN_REVIEW|BLOCKED|DONE|DEFERRED|CANCELLED)\s*\|",
            REGISTER.read_text(encoding="utf-8"),
            re.M,
        )
    }


def rendered(data: dict, controls: dict[str, dict]) -> dict[str, str]:
    """Generated blocks keyed by name."""
    blocks: dict[str, str] = {}
    summary = [
        "| Framework | Items | Evidenced | Partial | Documented | Gap | Out of scope |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for f in data["frameworks"]:
        counts = Counter(coverage(i, controls) for i in f["items"])
        summary.append(
            f"| {f['name']} | {len(f['items'])} | {counts['evidenced']} | {counts['partial']} | {counts['documented']} | {counts['gap']} | {counts['out_of_scope']} |"
        )
        rows = [
            "| Ref | Item | Applies | Coverage | Controls | Documents | Owner | What is missing, or why |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for i in f["items"]:
            cov = coverage(i, controls)
            missing = i["note"]
            if cov not in ("evidenced", "out_of_scope"):
                tail = (
                    f"closes in {i['next']}"
                    if i["next"]
                    else f"accepted: {i['accepted']}"
                    if i["accepted"]
                    else ""
                )
                missing = f"{missing} {tail}".strip()
            docs = ", ".join(f"`{Path(d).name}`" for d in i["docs"])
            rows.append(
                f"| {i['ref']} | {i['title']} | {i['applicability'].replace('_', ' ')} | {cov.replace('_', ' ')} | {', '.join(i['controls'])} | {docs} | {i['owner']} | {missing} |"
            )
        blocks[f["id"]] = "\n".join(rows)
    blocks["summary"] = "\n".join(summary)
    owner_rows = [
        "| Owner | Controls | Implemented | Partial | Planned |",
        "|---|---|---:|---:|---:|",
    ]
    by_owner: dict[str, list[dict]] = {}
    for c in controls.values():
        by_owner.setdefault(c["owner"], []).append(c)
    for owner, cs in sorted(by_owner.items()):
        counts = Counter(c["status"] for c in cs)
        ids = ", ".join(c["id"] for c in sorted(cs, key=lambda c: c["id"]))
        owner_rows.append(
            f"| {owner} | {ids} | {counts['implemented']} | {counts['partial']} | {counts['planned']} |"
        )
    blocks["owners"] = "\n".join(owner_rows)
    return blocks


def sync_doc(blocks: dict[str, str]) -> tuple[bool, str]:
    """Whether every generated block in the document equals the data's; with --render the blocks are rewritten."""
    if not DOC.is_file():
        return True, "skipped: documents are not in this checkout"
    text = DOC.read_text(encoding="utf-8")
    current, missing = True, []
    for name, body in blocks.items():
        begin, end = BEGIN.format(name=name), END.format(name=name)
        m = re.search(re.escape(begin) + r"\n(.*?)\n?" + re.escape(end), text, re.S)
        if not m:
            missing.append(name)
            current = False
            continue
        if m.group(1).strip() != body.strip():
            current = False
            if "--render" in sys.argv:
                text = text.replace(m.group(0), f"{begin}\n{body}\n{end}")
    if "--render" in sys.argv and not missing:
        DOC.write_text(text, encoding="utf-8")
        return True, "rendered"
    return current, f"missing blocks: {missing}" if missing else ""


def main() -> int:  # noqa: PLR0915
    controls = {c["id"]: c for c in json.loads(CONTROLS.read_text(encoding="utf-8"))["controls"]}
    data = fm.build()
    on_disk = (
        fm.OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n") if fm.OUTPUT.is_file() else ""
    )
    ev.check("the_json_on_disk_is_what_the_python_source_produces", on_disk == fm.render(data))

    frameworks = data["frameworks"]
    ev.check(
        "nine_frameworks_are_mapped", len(frameworks) == 9, ", ".join(f["id"] for f in frameworks)
    )
    refs = {f["id"]: [i["ref"] for i in f["items"]] for f in frameworks}
    ev.check("no_framework_lists_an_item_twice", all(len(r) == len(set(r)) for r in refs.values()))
    iso = refs["iso27001"]
    ev.check(
        "iso_27001_annex_a_is_complete_all_93_controls_none_missing_or_extra",
        sorted(iso) == sorted(ISO_EXPECTED) and len(iso) == 93,
        f"{len(iso)} items",
    )

    items = [(f["id"], i) for f in frameworks for i in f["items"]]
    cited = {c for _, i in items for c in i["controls"]}
    unknown = sorted(cited - set(controls))
    ev.check("every_cited_control_exists_in_the_catalogue", not unknown, str(unknown))
    orphans = sorted(set(controls) - cited)
    ev.check(
        "every_control_in_the_catalogue_is_cited_by_at_least_one_framework_item",
        not orphans,
        f"unmapped: {orphans}",
    )
    ev.check(
        "every_owner_is_a_real_role_code_and_every_applicability_is_valid",
        all(i["owner"] in OWNERS and i["applicability"] in APPLICABILITY for _, i in items),
    )

    tasks = register_tasks()
    docs_present = (REPO_ROOT / "docs").is_dir()
    bad_docs = sorted(
        {d for _, i in items for d in i["docs"] if docs_present and not (REPO_ROOT / d).exists()}
    )
    ev.check(
        "every_cited_document_exists",
        not bad_docs,
        str(bad_docs) if docs_present else "skipped: documents are not in this checkout",
    )
    bad_tasks = sorted(
        {i["next"] for _, i in items if i["next"] and tasks and i["next"] not in tasks}
    )
    ev.check(
        "every_named_next_task_exists_in_the_register",
        not bad_tasks,
        str(bad_tasks) if tasks else "skipped: the register is not in this checkout",
    )
    closed = sorted(
        {i["next"] for _, i in items if i["next"] and tasks.get(i["next"]) in ("DONE", "CANCELLED")}
    )
    ev.check(
        "no_gap_points_at_a_task_that_is_already_done_it_would_have_closed_it",
        not closed,
        str(closed) if tasks else "skipped: the register is not in this checkout",
    )

    cov = {(fid, i["ref"]): coverage(i, controls) for fid, i in items}
    unexplained = [
        f"{fid} {i['ref']}"
        for fid, i in items
        if cov[(fid, i["ref"])] in ("partial", "gap")
        and not (
            i["next"]
            or i["accepted"]
            or any(controls[c]["status"] != "implemented" for c in i["controls"])
        )
    ]  # a control that is not implemented already names its own gap, task and due date
    ev.check(
        "every_applicable_item_that_is_not_fully_evidenced_names_the_task_that_closes_it_or_why_it_is_accepted",
        not unexplained,
        str(unexplained[:10]),
    )
    unreasoned = [
        f"{fid} {i['ref']}"
        for fid, i in items
        if i["applicability"] == "not_applicable" and len(i["note"]) < 20
    ]
    ev.check("every_not_applicable_item_says_why", not unreasoned, str(unreasoned[:10]))
    over = [
        f"{fid} {i['ref']}"
        for fid, i in items
        if i["applicability"] == "not_applicable" and i["controls"]
    ]
    ev.check("a_not_applicable_item_cites_no_control", not over, str(over[:10]))

    claims = []
    for fid, i in items:
        for field in ("note", "accepted"):  # titles are the frameworks' own names
            if i[field] and CLAIM_WORDS.search(i[field]):
                claims.append(f"{fid} {i['ref']}")
    ev.check(
        "no_item_text_claims_compliance_conformity_or_certification", not claims, str(claims[:10])
    )
    ev.check(
        "the_top_level_statement_disclaims_any_compliance_conformity_or_certification_claim",
        bool(NEGATION.search(data["statement"])) and "not legal advice" in data["statement"],
    )
    if DOC.is_file():
        bad_lines = [
            n + 1
            for n, line in enumerate(DOC.read_text(encoding="utf-8").splitlines())
            if CLAIM_WORDS.search(line)
            and not NEGATION.search(line)
            and "BEGIN GENERATED" not in line
        ]
        ev.check(
            "the_document_uses_claim_words_only_in_sentences_that_deny_a_claim",
            not bad_lines,
            f"lines {bad_lines[:10]}",
        )
    else:
        ev.check(
            "the_document_uses_claim_words_only_in_sentences_that_deny_a_claim",
            True,
            "skipped: documents are not in this checkout",
        )

    blocks = rendered(data, controls)
    ok, detail = sync_doc(blocks)
    ev.check(
        "the_generated_tables_in_the_document_are_the_ones_the_data_produces",
        ok,
        detail or "run with --render to refresh them",
    )

    per_framework = {}
    for f in frameworks:
        per_framework[f["id"]] = dict(Counter(cov[(f["id"], i["ref"])] for i in f["items"]))
    ev.metrics["coverage_by_framework"] = per_framework
    ev.metrics["items"] = len(items)
    ev.metrics["controls_cited"] = len(cited)
    ev.metrics["gaps"] = sorted(
        f"{fid} {i['ref']} -> {i['next'] or 'accepted'}"
        for fid, i in items
        if cov[(fid, i["ref"])] == "gap"
    )
    ev.metrics["closing_tasks"] = dict(
        Counter(i["next"] for _, i in items if i["next"]).most_common()
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
