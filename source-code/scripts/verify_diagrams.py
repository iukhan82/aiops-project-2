#!/usr/bin/env python3
"""P13.01: the seven mandatory diagrams render, their checked-in exports match their sources, and each one keeps saying what P13.01 found to be true
of the tested implementation rather than drifting back to the first-draft (Phase 02) claims it corrected.

    python source-code/scripts/verify_diagrams.py

Needs Node and `npx` (it shells out to `@mermaid-js/mermaid-cli`, the same tool `diagrams/README.md` documents; the first run downloads it). Writes
`docs/evidence/p13_01_diagrams.json`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402

DIAGRAMS = REPO_ROOT / "diagrams"
SOURCES = DIAGRAMS / "sources"
EXPORTS = DIAGRAMS / "exports"

VIEWS = (
    "01-system-architecture",
    "02-network-flow",
    "03-data-flow",
    "04-workflow-command-safety-path",
    "05-security-trust-boundaries",
    "06-hybrid-cloud-placement",
    "07-deployment-topology",
    "08-proposed-production-reference",
)

# What each tested-implementation view must now say (found true by reading the real manifests and evidence, P13.01, 2026-09-27) and must never say again
# (the first-draft claim it corrected). A tuple entry is (view, must_contain) or (view, "NOT", must_not_contain).
CLAIMS: tuple[tuple[str, str] | tuple[str, str, str], ...] = (
    ("01-system-architecture", "AIOps"),
    ("01-system-architecture", "no separate collector"),
    ("01-system-architecture", "NOT", "OpenTelemetry Collector"),
    ("02-network-flow", "no separate collector"),
    ("02-network-flow", "not mutual TLS between them"),
    ("02-network-flow", "local stack only"),
    ("04-workflow-command-safety-path", "execution_gate"),
    ("04-workflow-command-safety-path", "D-03"),
    ("05-security-trust-boundaries", "Falco"),
    ("06-hybrid-cloud-placement", "kind cluster"),
    ("06-hybrid-cloud-placement", "NOT", "K3s namespace"),
    ("07-deployment-topology", "kind cluster"),
    ("07-deployment-topology", "aiops-command-executor"),
    ("07-deployment-topology", "aiops-platform-correlator"),
    ("07-deployment-topology", "NOT", "namespace: edge-sim"),
    ("08-proposed-production-reference", "NOT DEPLOYED"),
    ("08-proposed-production-reference", "NOT AUTHORIZED"),
)

# Real workload names a diagram may name as deployed on the target (infra/k8s/workloads.yaml); anything else claimed as a target workload is a diagram
# drifting from the manifests it is supposed to track.
REAL_WORKLOADS = {
    "aiops-api", "aiops-scenario-control", "aiops-command-executor", "aiops-outcome-verifier", "aiops-ingestion-gateway",
    "aiops-mqtt-gateway", "aiops-edge-runtime", "aiops-platform-correlator", "aiops-platform-probe", "aiops-frontend",
    "aiops-opa", "aiops-postgres", "aiops-kafka-broker", "aiops-mqtt-broker", "aiops-keycloak", "aiops-prometheus",
    "aiops-tempo", "aiops-loki", "aiops-grafana",
}  # fmt: skip
# On-demand Jobs (infra/k8s/jobs.yaml), not standing workloads, but real and nameable in a diagram that says so.
REAL_JOBS = {"aiops-migrate", "aiops-load", "aiops-security", "aiops-e2e"}


def npx() -> str:
    found = shutil.which("npx.cmd") or shutil.which("npx")
    if found is None:
        raise SystemExit("npx not found: node is needed to render the diagrams")
    return found


def _without_ids(svg: bytes) -> bytes:
    return re.sub(rb'id="[^"]*"', b"", svg)


def render(npx_path: str, source: Path, out: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [
            npx_path,
            "--yes",
            "@mermaid-js/mermaid-cli",
            "-i",
            str(source),
            "-o",
            str(out),
            "-b",
            "white",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-800:]


def main() -> int:
    ev = Evidence("P13.01", "p13_01_diagrams", docs_name="p13_01_diagrams")
    npx_path = npx()
    texts = {}

    failed_render = []
    stale_export = []
    with tempfile.TemporaryDirectory() as tmp:
        for view in VIEWS:
            source = SOURCES / f"{view}.mmd"
            export = EXPORTS / f"{view}.svg"
            texts[view] = source.read_text(encoding="utf-8")
            ok, tail = render(npx_path, source, Path(tmp) / f"{view}.svg")
            if not ok:
                failed_render.append((view, tail))
                continue
            fresh = (Path(tmp) / f"{view}.svg").read_bytes()
            checked_in = export.read_bytes() if export.is_file() else b""
            # Mermaid's SVG carries a run-specific id; compare the drawing content, not the wrapper attribute, so a checked-in export from an earlier
            # mermaid-cli run is not flagged as stale by that alone.
            if _without_ids(fresh) != _without_ids(checked_in):
                stale_export.append(view)
    ev.check(
        "every_diagram_source_renders_without_error",
        not failed_render,
        f"{[v for v, _ in failed_render]}: {failed_render[0][1] if failed_render else ''}",
    )
    ev.check(
        "every_checked_in_export_matches_a_fresh_render_of_its_source",
        not stale_export,
        f"stale, regenerate with mermaid-cli: {stale_export}",
    )

    bad_claims = []
    for entry in CLAIMS:
        if len(entry) == 2:
            view, must = entry
            if must not in texts[view]:
                bad_claims.append((view, "missing", must))
        else:
            view, _not, must_not = entry
            if must_not in texts[view]:
                bad_claims.append((view, "still says", must_not))
    ev.check(
        "each_diagram_still_says_what_p13_01_found_true_and_not_what_it_corrected",
        not bad_claims,
        str(bad_claims[:6]),
    )

    workloads_text = (SOURCE_ROOT / "infra" / "k8s" / "workloads.yaml").read_text(encoding="utf-8")
    jobs_text = (SOURCE_ROOT / "infra" / "k8s" / "jobs.yaml").read_text(encoding="utf-8")
    real = {
        name
        for name in REAL_WORKLOADS
        if re.search(rf"\bname: {re.escape(name)}\b", workloads_text)
    }
    real_jobs = {
        name for name in REAL_JOBS if re.search(rf"\bname: {re.escape(name)}\b", jobs_text)
    }
    missing_from_manifest = (REAL_WORKLOADS - real) | (REAL_JOBS - real_jobs)
    ev.check(
        "the_workload_and_job_names_this_verifier_checks_against_are_themselves_real",
        not missing_from_manifest,
        f"not found in the manifests: {missing_from_manifest}",
    )
    named_in_diagram = set(re.findall(r"\baiops-[a-z-]+\b", texts["07-deployment-topology"]))
    invented = named_in_diagram - REAL_WORKLOADS - REAL_JOBS
    ev.check(
        "diagram_07_names_no_target_workload_that_is_not_in_the_real_manifest",
        not invented,
        f"named but not real: {invented}",
    )

    readme = (DIAGRAMS / "README.md").read_text(encoding="utf-8")
    ev.check(
        "the_diagram_index_documents_this_review_and_its_limitations",
        "P13.01" in readme and "kind" in readme and "limitations" in readme,
        "diagrams/README.md",
    )
    ev.metrics = {"views": len(VIEWS), "real_workloads_checked": len(REAL_WORKLOADS)}
    ev.notes["what_this_does_not_check"] = (
        "visual legibility and layout are not checked by a script - the seven exports were opened and read after regeneration, not just rendered without a parser error"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
