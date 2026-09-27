#!/usr/bin/env python3
"""P12.06: a reproducible evidence bundle - every artifact indexed with the command that made it, the conditions, the versions, the failures and the requirements it supports.

    python source-code/acceptance/build_bundle.py            # writes docs/evidence/EVIDENCE_INDEX.md, docs/evidence/p12_06_evidence_index.json and the bundle archive

For every file under `docs/evidence/` the index records its SHA-256, its size, the task, when it was generated, whether it passed, how many checks it holds and which failed, the command that
regenerates it and where that command can run (the local stack, a real browser, the SUMO container, WSL, the target host), and the matrix rows and requirements that cite it. What is not a pass is
listed apart from what is: evidence that records a failure on purpose (a finding, a first run before a fix), runs that failed in this phase's sweep, and evidence that was NOT re-run in this phase
and how old it is. The archive is deterministic (sorted names, no timestamps, no owner): building it twice gives the same bytes, and its hash is recorded.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import platform
import subprocess
import sys
import tarfile
import time
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from acceptance import matrix_data as data  # noqa: E402
from acceptance import quality  # noqa: E402
from acceptance.runs import RUNS, TARGET_RUNS  # noqa: E402

EVIDENCE = REPO_ROOT / "docs" / "evidence"
OUTPUT = SOURCE_ROOT / "infra" / "platform" / "output"
RUN_RECORD = OUTPUT / "acceptance_runs.json"
SELF = {
    "EVIDENCE_INDEX.md",
    "p12_06_evidence_index.json",
    "p12_06_evidence_bundle.json",
}  # the index does not hash itself, nor the verifier's own evidence, which is written after it
PHASE_START = "2026-09-26T08:00:00Z"  # the sweep of this phase began after this

LABS = {
    "p12_02_safe02_protected_actions": (
        "python source-code/acceptance/lab_protected_actions.py",
        "local stack: PostgreSQL, the policy engine (stopped and started), a counting adapter",
    ),
    "p12_02_safe03_stale_state": (
        "python source-code/acceptance/lab_stale_state.py",
        "local stack: PostgreSQL",
    ),
    "p12_02_rec01_edge_outage": (
        "python source-code/acceptance/lab_edge_outage.py",
        "local stack: broker container (stopped and started), gateway, Kafka, ingestion, PostgreSQL; an edge process that is killed",
    ),
    "p12_02_rec02_executor_restart": (
        "python source-code/acceptance/lab_executor_restart.py",
        "local stack: PostgreSQL, the policy engine; an executor process that is killed",
    ),
    "p12_04_outcomes_by_group": (
        "python source-code/acceptance/analysis_groups.py",
        "workstation: the held-out congestion runs (a non-selecting, ledgered opening) and the P07.10 evidence",
    ),
    "p12_01_acceptance_matrix": (
        "python source-code/acceptance/verify_matrix.py p12_01",
        "workstation: reads docs/evidence",
    ),
    "p12_02_fault_matrix": (
        "python source-code/acceptance/verify_matrix.py p12_02",
        "workstation: reads docs/evidence",
    ),
    "p12_04_quality_matrix": (
        "python source-code/acceptance/verify_matrix.py p12_04",
        "workstation: reads docs/evidence and scans the source",
    ),
    "p12_05_defects": (
        "python source-code/acceptance/verify_defects.py",
        "workstation: reads docs/evidence and the register",
    ),
    "p12_07_coverage_review": (
        "python source-code/acceptance/verify_coverage.py",
        "workstation: reads docs/evidence",
    ),
    "P12_01_ACCEPTANCE_MATRIX": (
        "python source-code/acceptance/verify_matrix.py p12_01",
        "workstation: generated from docs/evidence",
    ),
    "P12_02_FAULT_MATRIX": (
        "python source-code/acceptance/verify_matrix.py p12_02",
        "workstation: generated from docs/evidence",
    ),
    "P12_04_QUALITY_MATRIX": (
        "python source-code/acceptance/verify_matrix.py p12_04",
        "workstation: generated from docs/evidence",
    ),
    "DEFECT_REGISTER": (
        "python source-code/acceptance/verify_defects.py",
        "workstation: generated from acceptance/defects.py and docs/evidence",
    ),
    "P12_07_COVERAGE_REVIEW": (
        "python source-code/acceptance/verify_coverage.py",
        "workstation: generated from docs/evidence",
    ),
    "p12_03_integrity_and_claims": (
        "python source-code/models/verify_integrity_and_claims.py",
        "workstation (some re-runs need the stack)",
    ),
    "p13_01_diagrams": (
        "python source-code/scripts/verify_diagrams.py",
        "workstation: needs Node/npx (mermaid-cli)",
    ),
    "p13_03_api_docs": (
        "python source-code/scripts/verify_api_docs.py",
        "workstation: imports the FastAPI app, no running server or database needed",
    ),
    "p13_03_docs": (
        "python source-code/scripts/verify_docs.py",
        "workstation",
    ),
    "p13_04_runbooks": (
        "python source-code/scripts/verify_runbooks.py",
        "workstation",
    ),
    "p13_05_publication_readiness": (
        "python source-code/scripts/verify_publication_readiness.py",
        "workstation: needs git and the frontend's package-lock.json; Python licences are read from the active venv's installed packages",
    ),
    "p13_06_release_manifest": (
        "python source-code/scripts/build_release_manifest.py",
        "workstation: needs git, Node and WSL Docker for the version report",
    ),
}
OTHER = {
    "bounds_report": ("python source-code/simulator/verification/verify_bounds.py", "workstation"),
    "p01_08_clean_start": (
        "python source-code/scripts/verify_clean_start.py",
        "workstation: clones the tree fresh and builds it (not part of the stack sweep - it starts from no checkout of its own)",
    ),
    "p08_09_demo_controls": (
        "python source-code/backend/scenario_control/verify_scenario_control.py",
        "stack (run by acceptance/run_catalog.py --run scenario-control): a second evidence file the same verifier writes, alongside p05_09_scenario_control",
    ),
    "p09_07_access_review": (
        "python source-code/security/access_review.py",
        "stack, Keycloak provisioned (run as a step of acceptance/run_catalog.py --run runtime-acceptance); "
        "--offline reviews only the declared side, no Keycloak needed",
    ),
    "edge_benchmark_report": (
        "python source-code/models/evaluation/build_report.py",
        "workstation: the edge runtime container under its resource limits (WSL Docker)",
    ),
    "p05_01_platform_provisioning": (
        "bash source-code/infra/platform/verify.sh",
        "WSL Docker: brings the stack up, restarts containers",
    ),
    "p05_02_mqtt_mtls_acl": (
        "bash source-code/infra/platform/mosquitto/verify-mtls.sh",
        "WSL Docker: the broker",
    ),
    "p06_02_baselines": (
        "python source-code/backend/analytics/evaluate_baselines.py",
        "workstation",
    ),
    "p06_03_training_report": (
        "python source-code/backend/analytics/train_forecast.py",
        "workstation (a non-selecting test opening is ledgered)",
    ),
    "p06_04_congestion_evaluation": (
        "python source-code/backend/analytics/evaluate_congestion.py",
        "workstation",
    ),
    "p06_06_vru_evaluation": (
        "python source-code/backend/analytics/evaluate_vru.py",
        "workstation",
    ),
    "p06_08_acceptance": (
        "python source-code/backend/analytics/evaluate_p06_08.py",
        "workstation: assembles the held-out numbers already ledgered",
    ),
    "p06_08_incident_evaluation": (
        "python source-code/backend/analytics/evaluate_intelligence.py",
        "workstation",
    ),
    "p09_08_supply_chain": (
        "python3 source-code/security/supply_chain/build_release.py",
        "WSL: Trivy, Syft and Cosign as pinned containers",
    ),
    "p10_09_incident_quality": (
        "python source-code/backend/aiops/evaluate_platform_incidents.py",
        "workstation: records a finding (FA-03), so it does not pass by design",
    ),
    "replay_report": ("python source-code/simulator/manifest/build_and_verify.py", "workstation"),
    "split_summary": ("python source-code/simulator/datasets/build_and_verify.py", "workstation"),
    "p11_06_target_load_first_run": (
        "verify_load_target.py on the target (see the runbook)",
        "the real host: the first run of the load check, kept because it records the defects D-04 and D-05",
    ),
    "p11_06_target_load_second_run": (
        "verify_load_target.py on the target (see the runbook)",
        "the real host: the second run, after the first fixes",
    ),
}
PACKAGES = (
    "fastapi",
    "uvicorn",
    "psycopg",
    "paho-mqtt",
    "confluent-kafka",
    "onnxruntime",
    "numpy",
    "scikit-learn",
    "jsonschema",
    "pytest",
    "ruff",
    "httpx",
    "PyJWT",
    "paramiko",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sh(*argv: str) -> str | None:
    try:
        return (
            subprocess.run(
                argv, capture_output=True, text=True, timeout=30, check=False
            ).stdout.strip()
            or None
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def versions() -> dict:
    from importlib import metadata  # noqa: PLC0415

    pkgs = {}
    for name in PACKAGES:
        try:
            pkgs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pkgs[name] = None
    return {
        "python": platform.python_version(), "platform": platform.platform(), "packages": pkgs, "node": sh("node", "--version"), "docker_in_wsl": sh("wsl.exe", "-e", "docker", "--version"),
        "git_commit": sh("git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"), "git_uncommitted_files": len((sh("git", "-C", str(REPO_ROOT), "status", "--porcelain") or "").splitlines()),
        "project_version": (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip() if (REPO_ROOT / "VERSION").exists() else None,
    }  # fmt: skip


def producers() -> dict[str, tuple[str, str]]:
    table: dict[str, tuple[str, str]] = {}
    for run in RUNS:
        needs = ", ".join(run.needs) or "workstation"
        for name in run.evidence:
            table[name] = (
                "python source-code/" + " ".join(run.argv),
                f"{needs} (run by acceptance/run_catalog.py --run {run.id})",
            )
    for run_id, task, title, names in TARGET_RUNS:
        for name in names:
            table[name] = (
                f"runbook step / verifier for {task} on the target ({run_id})",
                f"the real target host, kind cluster aiops-p11: {title}; the host was cleaned afterwards",
            )
    table.update(LABS)
    table.update({k: v for k, v in OTHER.items() if k not in table})
    return table


def cited_by() -> dict[str, list[dict]]:
    """evidence name -> the matrix rows that stand on it, with the requirements they link."""
    out: dict[str, list[dict]] = {}
    rows = [
        *data.SCENARIOS,
        *data.WORKFLOW_CLASSES,
        *data.FAULTS,
        *quality.SAFETY,
        *quality.PRIVACY,
        *quality.ACCESSIBILITY,
        *quality.FAIRNESS,
    ]
    for row in rows:
        for cite in row.cites:
            if not cite.file.startswith("pytest:"):
                out.setdefault(cite.file, []).append({"row": row.id, "links": list(row.links)})
    for capability, (positive, negative) in data.CAPABILITY_EVIDENCE.items():
        for cite in (*positive, *negative):
            out.setdefault(cite.file, []).append({"row": f"CAP {capability}", "links": []})
    return out


def run_summary() -> dict:
    if not RUN_RECORD.exists():
        return {"runs": [], "passed": [], "failed": [], "note": "no run record"}
    runs = json.loads(RUN_RECORD.read_text(encoding="utf-8"))["runs"]
    return {
        "runs": [{k: r[k] for k in ("id", "task", "title", "group", "command", "started", "seconds", "exit", "passed")} for r in runs],
        "passed": [r["id"] for r in runs if r["passed"]], "failed": [{"id": r["id"], "tail": r["output_tail"][-2:]} for r in runs if not r["passed"]],
        "not_run": [r.id for r in RUNS if r.id not in {x["id"] for x in runs}],
    }  # fmt: skip


def entries() -> list[dict]:
    table, cites = producers(), cited_by()
    out = []
    for path in sorted(EVIDENCE.iterdir()):
        if path.name in SELF or not path.is_file():
            continue
        stem = path.stem
        entry = {
            "file": f"docs/evidence/{path.name}",
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
            "kind": "document" if path.suffix == ".md" else "evidence",
        }
        if path.suffix == ".json":
            doc = json.loads(path.read_text(encoding="utf-8"))
            checks = doc.get("checks") if isinstance(doc, dict) else None
            tests = doc.get("tests") if isinstance(doc, dict) else None
            failing = (
                [k for k, v in checks.items() if not v]
                if isinstance(checks, dict)
                else (
                    [t["title"] for t in tests if t.get("status") != "passed"]
                    if isinstance(tests, list)
                    else []
                )
            )
            entry |= {
                "task": doc.get("task") if isinstance(doc, dict) else None, "generated_at": doc.get("generated_at") if isinstance(doc, dict) else None,
                "all_passed": doc.get("all_passed") if isinstance(doc, dict) else None, "checks": len(checks) if isinstance(checks, dict) else (len(tests) if isinstance(tests, list) else None), "failing": failing[:10],
            }  # fmt: skip
        command, conditions = table.get(
            stem,
            (
                "written and reviewed by hand (see the register row of its task)"
                if path.suffix == ".md"
                else "not recorded",
                "n/a" if path.suffix == ".md" else "not recorded",
            ),
        )
        entry |= {"command": command, "conditions": conditions, "cited_by": cites.get(stem, [])}
        generated = entry.get("generated_at") or ""
        if entry["kind"] == "document":
            entry["status"] = "document"
        elif entry.get("failing"):
            entry["status"] = "records a failure"
        elif generated and generated < PHASE_START:
            entry["status"] = "passed, not re-run in this phase"
        else:
            entry["status"] = "passed"
        out.append(entry)
    return out


def archive(files: list[Path], extra: dict[str, bytes]) -> bytes:
    """A deterministic tar.gz: sorted names, no timestamps, no owner."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        members = {
            **{str(f.relative_to(REPO_ROOT)).replace("\\", "/"): f.read_bytes() for f in files},
            **extra,
        }
        for name in sorted(members):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.uid, info.gid, info.uname, info.gname, info.mode = (
                len(members[name]),
                0,
                0,
                0,
                "",
                "",
                0o644,
            )
            tar.addfile(info, io.BytesIO(members[name]))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(buffer.getvalue())
    return out.getvalue()


def main() -> int:
    items = entries()
    summary = run_summary()
    manifest = "\n".join(f"{e['sha256']}  {e['file']}" for e in items)
    manifest_sha = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    failing_by_design = [e["file"] for e in items if e["status"] == "records a failure"]
    not_rerun = [
        (e["file"], (e.get("generated_at") or "")[:10])
        for e in items
        if e["status"] == "passed, not re-run in this phase"
    ]
    index = {
        "task": "P12.06", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "manifest_sha256": manifest_sha, "artifacts": len(items), "versions": versions(),
        "how_to_reproduce": [
            "python source-code/acceptance/run_catalog.py --list", "python source-code/acceptance/run_catalog.py --all   (about two hours on the local stack, longer with the AIOps labs)",
            "python source-code/acceptance/lab_protected_actions.py; lab_stale_state.py; lab_edge_outage.py; lab_executor_restart.py; analysis_groups.py",
            "python source-code/acceptance/verify_matrix.py p12_01 | p12_02 | p12_04; verify_defects.py; verify_coverage.py; build_bundle.py; verify_bundle.py",
            "the Phase 11 runs: docs/runbooks/TARGET_DEPLOYMENT_RUNBOOK.md on a host with kind, Docker and the shipped images",
        ],
        "sweep": summary, "records_a_failure_by_design": failing_by_design, "passed_but_not_re_run_in_this_phase": not_rerun, "entries": items,
    }  # fmt: skip
    (EVIDENCE / "p12_06_evidence_index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )

    lines = ["# Evidence index (P12.06)", "", "Generated by `source-code/acceptance/build_bundle.py`; do not edit by hand. Verified by `verify_bundle.py`.", "",
             f"{len(items)} artifacts. Manifest SHA-256 `{manifest_sha}`. Commit `{index['versions']['git_commit']}` ({index['versions']['git_uncommitted_files']} files changed since). Python {index['versions']['python']} on {index['versions']['platform']}.", "",
             "## Not a plain pass", ""]  # fmt: skip
    lines += [f"- records a failure on purpose: `{f}`" for f in failing_by_design] or ["- none"]
    lines += [
        f"- failed in this phase's sweep: `{f['id']}`: {' | '.join(f['tail'])[:200]}"
        for f in summary.get("failed", [])
    ]
    lines += [
        "",
        f"Not re-run in this phase (evidence older than the sweep): {len(not_rerun)}",
        "",
        *[f"- `{f}` from {d}" for f, d in not_rerun],
        "",
        "## Artifacts",
        "",
        "| File | Task | Generated | Result | Checks | Command | Conditions | Supports |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in items:
        support = ", ".join(sorted({f"{c['row']}" for c in e["cited_by"]}))[:120]
        lines.append(
            f"| `{Path(e['file']).name}` | {e.get('task') or ''} | {(e.get('generated_at') or '')[:10]} | {e['status']} | {e.get('checks') or ''} | `{e['command'][:90]}` | {e['conditions'][:90]} | {support} |".replace(
                "\n", " "
            )
        )
    (EVIDENCE / "EVIDENCE_INDEX.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )

    files = [
        p
        for p in sorted(EVIDENCE.iterdir())
        if p.is_file() and p.name != "p12_06_evidence_bundle.json"
    ]
    extra = {
        "docs/requirements/ACCEPTANCE_TARGETS.md": (
            REPO_ROOT / "docs" / "requirements" / "ACCEPTANCE_TARGETS.md"
        ).read_bytes(),
        "docs/requirements/TRACEABILITY.md": (
            REPO_ROOT / "docs" / "requirements" / "TRACEABILITY.md"
        ).read_bytes(),
    }
    if RUN_RECORD.exists():
        extra["runs/acceptance_runs.json"] = RUN_RECORD.read_bytes()
    first = archive(files, extra)
    second = archive(files, extra)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    name = f"evidence-bundle-{manifest_sha[:12]}.tar.gz"
    (OUTPUT / name).write_bytes(first)
    (OUTPUT / "evidence-bundle.sha256").write_text(
        f"{hashlib.sha256(first).hexdigest()}  {name}\n{hashlib.sha256(second).hexdigest()}  (second build)\n",
        encoding="utf-8",
    )
    print(
        f"{len(items)} artifacts indexed; manifest {manifest_sha[:16]}; bundle {name} {len(first)} bytes sha256 {hashlib.sha256(first).hexdigest()[:16]} (second build identical: {first == second})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
