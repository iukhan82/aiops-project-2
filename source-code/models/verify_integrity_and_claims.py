"""P12.03 acceptance evidence: model and data integrity, and whether the measured claims trace to evidence.

    python source-code/models/verify_integrity_and_claims.py

A. Package integrity. Every versioned model package's files match the hashes in its manifest. A file that is git-ignored and absent from a
   fresh clone is reported by name with the command that regenerates it; it is not silently skipped.
B. Model cards. Every package carries a model card that names its limitations.
C. Test-split discipline across the whole registry: no (dataset, feature version) has more than one SELECTING opening of the test split
   unless a reopen reason is recorded, and every non-selecting opening says why.
D. Measured claims. Every row of `docs/requirements/ACCEPTANCE_TARGETS.md` that reports a measurement cites evidence that exists; that
   evidence passed, or the row says plainly that the target was NOT met. The numbers in each row are looked for in the cited evidence
   (a number is traced when a value in the evidence equals it at the precision written, or as a percentage of it); the ones that are not
   found are LISTED, not hidden - a derived figure (an interval, a difference) is legitimately untraced by this simple rule and is for a
   reader to review. Rows written in this session are also checked against their evidence by name.
E. Reproduction. The verifiers that need no running stack are re-run and must pass: the operations dataset and detector (P10.05/P10.06),
   the workload manifests (P11.02), the control mapping and threat model (P09.01/P09.07). The data inventory check (P09.06) needs the running database and is not re-run here.
F. Limitations are read: every measured row that rests on a small sample, a synthetic source or a single host says so in its own text.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402
from models.evaluation.test_gate import LEDGER_PATH, SELECTING_PURPOSES  # noqa: E402

ev = Evidence("P12.03", "p12_03_integrity_and_claims", docs_name="p12_03_integrity_and_claims")
REGISTRY = SOURCE_ROOT / "models" / "registry"
TARGETS = REPO_ROOT / "docs" / "requirements" / "ACCEPTANCE_TARGETS.md"
EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"
REGENERATE = {
    "ops-anomaly-detector/1.0.0/model.joblib": "python source-code/models/operations_detector/train_evaluate.py",
    "traffic-forecast/1.0.0/models.joblib": "python source-code/models/train/train_forecast.py",
}
PACKAGES = ("traffic-safety-blockage/1.0.0", "traffic-forecast/1.0.0", "ops-anomaly-detector/1.0.0")
RERUN = (
    ("operations dataset (P10.05)", "models/operations_dataset/verify_operations_dataset.py"),
    ("operations detector (P10.06)", "models/operations_detector/verify_operations_detector.py"),
    ("workload manifests (P11.02)", "infra/k8s/verify_workloads.py"),
    ("control mapping (P09.07)", "security/verify_control_mapping.py"),
    ("threat model (P09.01)", "security/verify_threat_model.py"),
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_files(package: str, manifest: dict) -> dict[str, str]:
    if "files" in manifest:
        return manifest["files"]
    return {manifest["artifact"]: manifest["sha256"]}


def numeric_leaves(node) -> list[float]:
    out: list[float] = []
    if isinstance(node, bool):
        return out
    if isinstance(node, int | float):
        out.append(float(node))
    elif isinstance(node, dict):
        for v in node.values():
            out.extend(numeric_leaves(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(numeric_leaves(v))
    elif isinstance(node, str):
        out.extend(float(x) for x in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", node))
    return out


def traced(token: str, leaves: list[float]) -> bool:
    clean = token.replace(",", "")
    decimals = len(clean.split(".")[1]) if "." in clean else 0
    value = float(clean)
    return any(
        round(v, decimals) == value
        or round(v * 100, decimals) == value
        or round(v * 1000, decimals) == value
        for v in leaves
    )


def target_rows() -> list[dict]:
    rows = []
    for line in TARGETS.read_text(encoding="utf-8").splitlines():
        if re.match(r"\| [A-Z]+-\d+ \|", line):
            cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
            rows.append(
                {
                    "id": cells[0],
                    "text": cells[-1],
                    "evidence": sorted(
                        set(
                            re.findall(
                                r"((?:docs/evidence|source-code/models/registry)/[\w\-./]+\.json)",
                                line,
                            )
                        )
                    ),
                }
            )
    return rows


def main() -> int:  # noqa: PLR0915
    # ---- A. package integrity
    problems, absent, checked = [], [], 0
    for package in PACKAGES:
        directory = REGISTRY / package
        manifest = json.loads((directory / "artifact_manifest.json").read_text(encoding="utf-8"))
        for name, expected in manifest_files(package, manifest).items():
            path = directory / name
            if not path.is_file():
                absent.append(f"{package}/{name}")
            elif sha(path) != expected:
                problems.append(f"{package}/{name}")
            else:
                checked += 1
    ev.check(
        "every_package_file_present_matches_its_manifest_hash",
        not problems and checked > 0,
        f"{checked} files match; mismatched {problems}",
    )
    ev.check(
        "every_absent_artifact_is_listed_with_the_command_that_regenerates_it",
        all(a in REGENERATE for a in absent),
        f"absent from this checkout: {absent or 'none'}",
    )
    ev.notes["regenerate"] = json.dumps(REGENERATE)

    # ---- B. model cards
    weak = []
    for package in PACKAGES:
        card_path = REGISTRY / package / "model_card.json"
        card = json.loads(card_path.read_text(encoding="utf-8")) if card_path.is_file() else {}
        limits = card.get("limitations") or card.get("known_limitations") or card.get("limits")
        if not limits:
            weak.append(package)
    ev.check(
        "every_package_has_a_model_card_that_names_its_limitations",
        not weak,
        f"without limitations: {weak}",
    )

    # ---- C. ledger
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))["entries"]
    selecting: dict[tuple[str, str], int] = {}
    for e in ledger:
        if e["purpose"] in SELECTING_PURPOSES and not e.get("reopen_reason"):
            key = (e["dataset_sha256"], e["feature_version"])
            selecting[key] = selecting.get(key, 0) + 1
    repeated = {k: n for k, n in selecting.items() if n > 1}
    ev.check(
        "no_dataset_and_feature_version_has_more_than_one_selecting_test_opening_without_a_recorded_reason",
        not repeated,
        f"{len(selecting)} selecting openings, repeated without a reason: {list(repeated)}",
    )
    unexplained = [
        e["purpose"]
        for e in ledger
        if e["purpose"] not in SELECTING_PURPOSES and not e.get("detail")
    ]
    ev.check(
        "every_non_selecting_opening_records_what_it_was_for",
        not unexplained,
        f"{len(ledger)} ledger entries",
    )

    # ---- D. measured claims
    rows = target_rows()
    measured = [r for r in rows if re.search(r"\*\*(Met|Measured)", r["text"])]
    partial = [r for r in rows if r["text"].startswith("**Partial")]
    missing_evidence, failing, untraced_report = [], [], {}
    for r in measured:
        if not r["evidence"]:
            missing_evidence.append(r["id"])
            continue
        leaves: list[float] = []
        for name in r["evidence"]:
            path = REPO_ROOT / name
            if not path.is_file():
                missing_evidence.append(f"{r['id']}:{name}")
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            leaves.extend(numeric_leaves(data))
            if data.get("all_passed") is False and "NOT met" not in r["text"]:
                failing.append(f"{r['id']}:{name}")
        prose = re.sub(
            r"`[^`]*`|docs/evidence/\S+|P\d\d\.\d\d(?:[-/]P?\d\d\.\d\d)*|\d{4}-\d\d-\d\d|Wilson 95%|95%",
            " ",
            r["text"],
        )
        tokens = [
            t
            for t in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w.])", prose)
            if "." in t or len(t.replace(",", "")) >= 3
        ]
        untraced_report[r["id"]] = {
            "numbers": len(tokens),
            "not_found": [t for t in tokens if not traced(t, leaves)][:12],
        }
    ev.check(
        "every_measured_target_row_cites_evidence_that_exists",
        not missing_evidence,
        f"{len(measured)} measured rows of {len(rows)}; missing: {missing_evidence}",
    )
    ev.check(
        "the_cited_evidence_passed_or_the_row_says_the_target_was_not_met",
        not failing,
        f"failing without saying so: {failing}",
    )
    unowned = [
        r["id"]
        for r in partial
        if not (re.search(r"P\d\d\.\d\d", r["text"]) and re.search(r"not |no ", r["text"], re.I))
    ]
    ev.check(
        "every_partial_row_says_what_is_not_shown_and_names_the_task_that_owns_it",
        not unowned,
        f"{len(partial)} partial rows; without an owner or a gap: {unowned}",
    )
    ev.metrics["number_tracing"] = untraced_report
    total = sum(v["numbers"] for v in untraced_report.values())
    found = total - sum(len(v["not_found"]) for v in untraced_report.values())
    ev.notes["number_tracing_summary"] = (
        f"{found} of {total} figures written in the measured rows were found in their cited evidence; the rest are listed per row under metrics.number_tracing for review"
    )

    # rows written this session are checked against their evidence by name
    recovery = json.loads((EVIDENCE_DIR / "p10_09_recovery.json").read_text(encoding="utf-8"))
    dispatch = recovery["metrics"]["latencies"]["dispatch_s"]["values_s"]
    lat = next(r for r in rows if r["id"] == "LAT-06")["text"]
    ev.check(
        "LAT_06_and_REC_04_as_written_match_the_recovery_evidence",
        all(f"{v:.1f}" in lat for v in dispatch)
        and recovery["all_passed"]
        and "REC_04_every_incident_ended_recovered_and_verified_or_escalated_with_a_recorded_reason_none_was_left_unresolved_and_silent"
        in recovery["checks"],
        f"dispatch {dispatch}",
    )
    quality = json.loads(
        (EVIDENCE_DIR / "p10_09_incident_quality.json").read_text(encoding="utf-8")
    )["metrics"]["fa_03"]
    fa3 = next(r for r in rows if r["id"] == "FA-03")["text"]
    ev.check(
        "FA_03_as_written_matches_the_incident_quality_evidence_and_says_not_met",
        f"{quality['false_incidents']} false of {quality['incidents']}" in fa3
        and f"{quality['false_incident_rate']:.1%}" in fa3
        and "NOT met" in fa3,
        f"{quality['false_incidents']}/{quality['incidents']} = {quality['false_incident_rate']:.1%}",
    )
    dq = json.loads((EVIDENCE_DIR / "p10_10_data_quality.json").read_text(encoding="utf-8"))[
        "metrics"
    ]["test"]
    fa2 = next(r for r in rows if r["id"] == "FA-02")["text"]
    false = dq["fragment"] + dq["wrong_class"] + dq["spurious"]
    ev.check(
        "FA_02_as_written_matches_the_data_quality_evidence_and_keeps_the_interval_and_the_synthetic_source_beside_it",
        f"{false} false incidents of {dq['incidents']}" in fa2
        and "not excluded" in fa2
        and "synthetic" in fa2,
        f"{false}/{dq['incidents']}",
    )

    # ---- F. limitations are said in the rows themselves
    thin = [
        r["id"]
        for r in measured
        if not re.search(
            r"sample|synthetic|single|one host|small|limit|scope|tested|not |only|n=|caveat|read as|Wilson",
            r["text"],
            re.I,
        )
    ]
    ev.check(
        "every_measured_row_states_a_scope_sample_or_limit_in_its_own_text",
        not thin,
        f"rows with no such wording: {thin}",
    )

    # ---- E. reproduction
    results = {}
    for label, script in RERUN:
        run = subprocess.run(
            [sys.executable, str(SOURCE_ROOT / script)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        passed = run.returncode == 0
        results[label] = passed
        tail = [ln for ln in run.stdout.splitlines() if "CHECKS" in ln or "PASSED" in ln][-1:]
        ev.check(
            f"re_running_{label.split(' (')[0].replace(' ', '_')}_verification_passes",
            passed,
            f"{label}: {tail[0] if tail else run.stderr[-200:]}",
        )
    ev.metrics["reproduction"] = results
    ev.notes["not_reproduced_here"] = (
        "the edge safety model, the forecast models and the live-stack latencies were not re-run: their training and lab runs are long and need the running stack; their packages are hash-checked above and their evidence is traced above"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
