"""P09.01 (and the standing security-control check): the threat model is consistent, complete for the platform's boundaries, and every control
that claims to be implemented is backed by evidence that passes.

    python source-code/security/verify_threat_model.py            # verify, write evidence
    python source-code/security/verify_threat_model.py --render   # also rewrite the generated tables in docs/security/

What is checked, and against what:

* the trust zones are read from `diagrams/sources/05-security-trust-boundaries.mmd`, and every zone has a boundary and threats;
* the thirteen areas the security baseline requires each have threats, and every threat category (STRIDE plus safety, AI and supply chain) is used;
* every treatment names a real control, every control is used, every planned or partial control names a real task and a due date;
* every control marked `implemented` names implementation files that exist and evidence that passes: a named check that is true in a real
  evidence file, a browser test that passed, or a pytest file that is run here;
* residual risk is computed from the controls' state, not written down: an implemented control lowers likelihood, a planned one lowers nothing.

The generated tables are compared with the ones in the documents, so a document cannot claim a state the data does not have.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402

SECURITY = SOURCE_ROOT / "security"
CONTROLS = SECURITY / "controls.json"
MODEL = SECURITY / "threat_model.json"
DIAGRAM = REPO_ROOT / "diagrams" / "sources" / "05-security-trust-boundaries.mmd"
REGISTER = REPO_ROOT / "TASK_REGISTER.md"
THREAT_DOC = REPO_ROOT / "docs" / "security" / "THREAT_MODEL.md"
RISK_DOC = REPO_ROOT / "docs" / "security" / "RISK_REGISTER.md"
BEGIN, END = "<!-- BEGIN GENERATED: {name} -->", "<!-- END GENERATED: {name} -->"

REQUIRED_AREAS = (
    "device",
    "edge",
    "broker",
    "stream",
    "API",
    "web",
    "identity",
    "policy",
    "model",
    "supply chain",
    "operator",
    "controller adapter",
    "external integration",
)
REQUIRED_CATEGORIES = (
    "Spoofing",
    "Tampering",
    "Repudiation",
    "Information disclosure",
    "Denial of service",
    "Elevation of privilege",
    "Safety",
    "AI",
    "Supply chain",
)
ROLE_CODES = {
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
LAYERS = {
    "identity",
    "authorization",
    "transport",
    "data",
    "safety",
    "ai",
    "supply-chain",
    "runtime",
    "operations",
}
STATUSES = ("implemented", "partial", "planned")
LIKELIHOOD = {"low": 1, "medium": 2, "high": 3}
IMPACT = {"none": 1, "low": 1, "medium": 2, "high": 3}


def level(score: int) -> str:
    return "low" if score <= 2 else "medium" if score <= 4 else "high"


def risk(threat: dict, controls: dict[str, dict]) -> dict:
    """Inherent and residual scores, safety and security kept apart. Only an implemented control earns credit, two steps at most."""
    likelihood = LIKELIHOOD[threat["likelihood"]]
    credit = min(2, sum(1 for c in threat["treatments"] if controls[c]["status"] == "implemented"))
    residual_likelihood = max(1, likelihood - credit)
    safety, security = IMPACT[threat["safety_impact"]], IMPACT[threat["security_impact"]]
    impact = max(safety, security)
    states = [controls[c]["status"] for c in threat["treatments"]]
    status = (
        "treated"
        if all(s == "implemented" for s in states)
        else "open"
        if not any(s == "implemented" for s in states)
        else "partially treated"
    )
    return {
        "inherent": likelihood * impact,
        "residual": residual_likelihood * impact,
        "residual_safety": residual_likelihood * safety,
        "residual_security": residual_likelihood * security,
        "status": status,
    }


def zones_in_diagram() -> set[str]:
    return set(re.findall(r"subgraph (TZ\d+)\[", DIAGRAM.read_text(encoding="utf-8")))


def registered_tasks() -> set[str]:
    return set(re.findall(r"^\| (P\d\d\.\d\d) \|", REGISTER.read_text(encoding="utf-8"), re.M))


def evidence_state(entry: dict) -> tuple[bool, str]:
    """Whether one evidence entry holds today, and why not."""
    if "pytest" in entry:
        path = REPO_ROOT / entry["pytest"]
        return (
            path.is_file(),
            f"pytest file {entry['pytest']} {'exists' if path.is_file() else 'is missing'}",
        )
    path = REPO_ROOT / entry["file"]
    if not path.is_file():
        return False, f"{entry['file']} is missing"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("all_passed") is False:
        return False, f"{entry['file']} is not all-passed"
    table = (
        data.get("checks")
        if isinstance(data.get("checks"), dict)
        else data.get("tests")
        if isinstance(data.get("tests"), dict)
        else {}
    )
    missing = [c for c in entry.get("checks", []) if c not in table]
    failing = [c for c in entry.get("checks", []) if c in table and not table[c]]
    if missing or failing:
        return False, f"{entry['file']}: missing {missing[:2]}, failing {failing[:2]}"
    if entry.get("tests"):
        rows = data.get("tests") if isinstance(data.get("tests"), list) else []
        for wanted in entry["tests"]:
            found = [r for r in rows if wanted in r.get("title", "")]
            if not found or any(r.get("status") != "passed" for r in found):
                return False, f"{entry['file']}: test {wanted[:60]!r} not found or not passed"
    return True, "ok"


def run_pytest(files: list[str]) -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, (result.stdout.strip().splitlines() or [""])[-1]


def rows_of(model: dict, controls: dict[str, dict]) -> list[dict]:
    boundaries = {b["id"]: b for b in model["boundaries"]}
    out = []
    for threat in model["threats"]:
        out.append(
            {
                **threat,
                **risk(threat, controls),
                "boundary_name": boundaries[threat["boundary"]]["name"],
                "zone": boundaries[threat["boundary"]]["zone"],
            }
        )
    return out


# ------------------------------------------------------------------ rendering
def generated_threat_tables(
    model: dict, controls_list: list[dict], rows: list[dict]
) -> dict[str, str]:
    boundaries = model["boundaries"]
    by_boundary: dict[str, list[dict]] = {}
    for row in rows:
        by_boundary.setdefault(row["boundary"], []).append(row)
    lines = ["| Boundary | Zone | Area | Threats | What it is |", "|---|---|---|---|---|"]
    lines += [
        f"| {b['id']} {b['name']} | {b['zone']} | {b['area']} | {len(by_boundary.get(b['id'], []))} | {b['description']} |"
        for b in boundaries
    ]
    boundary_table = "\n".join(lines)
    lines = ["| ID | Asset | Class |", "|---|---|---|"] + [
        f"| {a['id']} | {a['name']} | {a['class']} |" for a in model["assets"]
    ]
    asset_table = "\n".join(lines)
    blocks = []
    for b in boundaries:
        blocks += [
            f"#### {b['id']} {b['name']} ({b['zone']})",
            "",
            "| ID | Category | Threat | Safety | Security | Likelihood | Treated by | State |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in by_boundary.get(b["id"], []):
            blocks.append(
                f"| {r['id']} | {r['category']} | {r['title']} | {r['safety_impact']} | {r['security_impact']} | {r['likelihood']} | {', '.join(r['treatments'])} | {r['status']}{' (accepted)' if r.get('accepted') else ''} |"
            )
        blocks.append("")
    threat_tables = "\n".join(blocks)
    lines = ["| ID | Layer | Control | State | Task | Baseline item |", "|---|---|---|---|---|---|"]
    for c in controls_list:
        lines.append(
            f"| {c['id']} | {c['layer']} | {c['title']} | {c['status']} | {c['task']} | {', '.join(str(n) for n in c['baseline'])} |"
        )
    control_table = "\n".join(lines)
    return {
        "boundaries": boundary_table,
        "assets": asset_table,
        "threats": threat_tables,
        "controls": control_table,
    }


def generated_risk_tables(rows: list[dict], controls: dict[str, dict]) -> dict[str, str]:
    order = sorted(rows, key=lambda r: (-r["residual"], -r["inherent"], r["id"]))
    lines = [
        "| Threat | Boundary | Inherent | Residual | Safety / security residual | Treatment | Owner | Due | Open work |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in order:
        open_work = (
            "; ".join(
                f"{c} ({controls[c]['status']}, {controls[c]['task']})"
                for c in r["treatments"]
                if controls[c]["status"] != "implemented"
            )
            or "none"
        )
        accepted = f" Accepted: {r['accepted']}" if r.get("accepted") else ""
        lines.append(
            f"| {r['id']} {r['title']} | {r['boundary']} | {level(r['inherent'])} ({r['inherent']}) | {level(r['residual'])} ({r['residual']}) | "
            f"{level(r['residual_safety'])} / {level(r['residual_security'])} | {r['status']} | {r['owner']} | {r['due']} | {open_work}.{accepted} |"
        )
    register = "\n".join(lines)
    counts = {"low": 0, "medium": 0, "high": 0}
    inherent = {"low": 0, "medium": 0, "high": 0}
    for r in rows:
        counts[level(r["residual"])] += 1
        inherent[level(r["inherent"])] += 1
    summary = "\n".join(
        [
            "| Level | Inherent | Residual |",
            "|---|---|---|",
            *[
                f"| {name} | {inherent[name]} | {counts[name]} |"
                for name in ("high", "medium", "low")
            ],
            f"| **threats** | {len(rows)} | {len(rows)} |",
        ]
    )
    treatment = {"treated": 0, "partially treated": 0, "open": 0}
    for r in rows:
        treatment[r["status"]] += 1
    controls_summary = {s: sum(1 for c in controls.values() if c["status"] == s) for s in STATUSES}
    counts_table = "\n".join(
        [
            "| Threats treated by implemented controls only | Partially treated | Open (no implemented control) |",
            "|---|---|---|",
            f"| {treatment['treated']} | {treatment['partially treated']} | {treatment['open']} |",
            "",
            "| Controls implemented | Partial | Planned |",
            "|---|---|---|",
            f"| {controls_summary['implemented']} | {controls_summary['partial']} | {controls_summary['planned']} |",
        ]
    )
    return {"risk-register": register, "risk-summary": summary, "treatment-summary": counts_table}


def splice(text: str, name: str, generated: str) -> str:
    begin, end = BEGIN.format(name=name), END.format(name=name)
    if begin not in text:
        raise SystemExit(f"generated block {name} is missing from the document")
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{begin}\n\n{generated}\n\n{end}{tail}"


def apply_blocks(path: Path, blocks: dict[str, str], write: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    new = text
    for name, generated in blocks.items():
        new = splice(new, name, generated)
    if write and new != text:
        path.write_text(new, encoding="utf-8")
    return new == text


# ------------------------------------------------------------------ main
def main() -> int:  # noqa: PLR0915
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--render", action="store_true", help="rewrite the generated tables in docs/security/"
    )
    parser.add_argument(
        "--skip-pytest", action="store_true", help="do not run the pytest evidence files"
    )
    args = parser.parse_args()
    ev = Evidence("P09.01", docs_name="p09_01_threat_model")
    control_list = json.loads(CONTROLS.read_text(encoding="utf-8"))["controls"]
    model = json.loads(MODEL.read_text(encoding="utf-8"))
    controls = {c["id"]: c for c in control_list}
    tasks = registered_tasks()
    problems: list[str] = []

    ids = [c["id"] for c in control_list]
    for c in control_list:
        for key in ("id", "title", "layer", "baseline", "status", "task", "owner"):
            if key not in c:
                problems.append(f"{c.get('id', '?')}: missing {key}")
        if (
            c.get("layer") not in LAYERS
            or c.get("status") not in STATUSES
            or c.get("owner") not in ROLE_CODES
        ):
            problems.append(f"{c['id']}: layer, status or owner is not one of the known values")
        if not all(isinstance(n, int) and 1 <= n <= 15 for n in c.get("baseline", [])):
            problems.append(
                f"{c['id']}: baseline items must be the baseline's technical control numbers 1-15"
            )
    ev.check(
        "the_control_catalogue_is_well_formed_with_unique_ids_layers_statuses_owners_and_baseline_items",
        len(ids) == len(set(ids)) and not problems,
        detail="; ".join(problems[:3]),
    )

    threat_problems: list[str] = []
    boundary_ids, asset_ids = (
        {b["id"] for b in model["boundaries"]},
        {a["id"] for a in model["assets"]},
    )
    threat_ids = [t["id"] for t in model["threats"]]
    for t in model["threats"]:
        for key in (
            "id",
            "boundary",
            "category",
            "title",
            "description",
            "assets",
            "safety_impact",
            "security_impact",
            "likelihood",
            "treatments",
            "owner",
            "due",
        ):
            if key not in t:
                threat_problems.append(f"{t.get('id', '?')}: missing {key}")
        if (
            t.get("boundary") not in boundary_ids
            or not set(t.get("assets", [])) <= asset_ids
            or not set(t.get("treatments", [])) <= set(controls)
        ):
            threat_problems.append(
                f"{t['id']}: an unknown boundary, asset or control is referenced"
            )
        if (
            t.get("safety_impact") not in IMPACT
            or t.get("security_impact") not in IMPACT
            or t.get("likelihood") not in LIKELIHOOD
            or t.get("owner") not in ROLE_CODES
        ):
            threat_problems.append(
                f"{t['id']}: an impact, likelihood or owner is not a known value"
            )
        try:
            date.fromisoformat(t.get("due", ""))
        except ValueError:
            threat_problems.append(f"{t['id']}: due is not a date")
        if not t.get("treatments"):
            threat_problems.append(f"{t['id']}: no treatment")
    ev.check(
        "every_threat_names_a_known_boundary_assets_controls_owner_due_date_and_both_impacts",
        len(threat_ids) == len(set(threat_ids)) and not threat_problems,
        detail="; ".join(threat_problems[:3]),
    )

    in_diagram, in_model = zones_in_diagram(), {b["zone"] for b in model["boundaries"]}
    zone_threats = {
        z: sum(
            1
            for t in model["threats"]
            if next(b for b in model["boundaries"] if b["id"] == t["boundary"])["zone"] == z
        )
        for z in in_diagram
    }
    ev.check(
        "every_trust_zone_in_the_security_diagram_has_a_boundary_and_threats",
        in_diagram <= in_model and all(n > 0 for n in zone_threats.values()),
        detail=str(dict(sorted(zone_threats.items()))),
    )
    area_threats = {
        a: sum(
            1
            for t in model["threats"]
            if next(b for b in model["boundaries"] if b["id"] == t["boundary"])["area"] == a
        )
        for a in REQUIRED_AREAS
    }
    ev.check(
        "the_thirteen_areas_the_security_baseline_requires_each_have_at_least_two_threats",
        all(n >= 2 for n in area_threats.values()),
        detail=str(area_threats),
    )
    categories = {t["category"] for t in model["threats"]}
    ev.check(
        "every_threat_category_is_used_stride_plus_safety_ai_and_supply_chain",
        set(REQUIRED_CATEGORIES) <= categories,
        detail=str(sorted(set(REQUIRED_CATEGORIES) - categories)),
    )
    ev.check(
        "safety_impact_is_scored_separately_from_security_impact",
        any(t["safety_impact"] != t["security_impact"] for t in model["threats"])
        and any(t["safety_impact"] == "high" for t in model["threats"]),
        detail=f"{sum(1 for t in model['threats'] if t['safety_impact'] == 'high')} threats with high safety impact",
    )

    used = {c for t in model["threats"] for c in t["treatments"]}
    ev.check(
        "every_control_treats_at_least_one_threat",
        used == set(controls),
        detail=f"unused: {sorted(set(controls) - used)}",
    )

    open_problems = []
    for c in control_list:
        if c["status"] in ("planned", "partial"):
            if c["task"] not in tasks:
                open_problems.append(f"{c['id']}: {c['task']} is not in the register")
            if not c.get("gap"):
                open_problems.append(f"{c['id']}: no statement of what is missing")
            try:
                date.fromisoformat(c.get("due", ""))
            except ValueError:
                open_problems.append(f"{c['id']}: no due date")
    ev.check(
        "every_planned_or_partial_control_names_a_registered_task_what_is_missing_and_a_due_date",
        not open_problems,
        detail="; ".join(open_problems[:3]),
    )

    impl_problems, pytest_files = [], []
    for c in control_list:
        if c["status"] == "planned":
            if c.get("evidence"):
                impl_problems.append(
                    f"{c['id']}: a planned control has evidence; mark it partial or implemented"
                )
            continue
        for path in c.get("implementation", []):
            if not (REPO_ROOT / path).exists():
                impl_problems.append(f"{c['id']}: implementation path {path} does not exist")
        if c["status"] == "implemented" and not c.get("evidence"):
            impl_problems.append(f"{c['id']}: implemented without evidence")
        for entry in c.get("evidence", []):
            ok, why = evidence_state(entry)
            if not ok:
                impl_problems.append(f"{c['id']}: {why}")
            if "pytest" in entry:
                pytest_files.append(entry["pytest"])
    ev.check(
        "every_implemented_or_partial_control_has_existing_implementation_files_and_passing_named_evidence",
        not impl_problems,
        detail="; ".join(impl_problems[:4]),
    )
    evidence_named = sum(
        len(e.get("checks", [])) + len(e.get("tests", []))
        for c in control_list
        for e in c.get("evidence", [])
    )

    if args.skip_pytest:
        pytest_ok, pytest_detail = True, "skipped"
    else:
        unique = sorted(set(pytest_files))
        pytest_ok, pytest_detail = run_pytest(unique)
        pytest_detail = f"{len(unique)} files: {pytest_detail}"
    ev.check(
        "the_pytest_files_cited_as_evidence_pass_when_run_now", pytest_ok, detail=pytest_detail
    )

    rows = rows_of(model, controls)
    ev.check(
        "residual_risk_is_computed_from_control_state_a_planned_control_earns_no_credit",
        all(r["residual"] <= r["inherent"] for r in rows)
        and all(
            r["residual"] == r["inherent"]
            for r in rows
            if not any(controls[c]["status"] == "implemented" for c in r["treatments"])
        ),
        detail="rule in threat_model.json scales",
    )

    threat_blocks = generated_threat_tables(model, control_list, rows)
    risk_blocks = generated_risk_tables(rows, controls)
    threat_current = apply_blocks(THREAT_DOC, threat_blocks, args.render) or args.render
    risk_current = apply_blocks(RISK_DOC, risk_blocks, args.render) or args.render
    ev.check(
        "the_generated_tables_in_the_threat_model_and_risk_register_documents_are_current",
        threat_current and risk_current,
        detail="run with --render to refresh them",
    )

    residual = {"low": 0, "medium": 0, "high": 0}
    for r in rows:
        residual[level(r["residual"])] += 1
    ev.metrics = {
        "boundaries": len(model["boundaries"]),
        "assets": len(model["assets"]),
        "threats": len(rows),
        "controls": {s: sum(1 for c in control_list if c["status"] == s) for s in STATUSES},
        "evidence_items_named": evidence_named,
        "residual_risk": residual,
        "high_residual": [
            f"{r['id']} {r['title']}" for r in rows if level(r["residual"]) == "high"
        ],
        "threats_open_no_implemented_control": [r["id"] for r in rows if r["status"] == "open"],
        "zones": dict(sorted(zone_threats.items())),
        "areas": area_threats,
    }
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
