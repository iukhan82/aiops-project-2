"""P09.10 evidence: the security controls, exercised again against the running stack, and what the evidence does and does not cover.

    python source-code/security/verify_runtime_acceptance.py          # needs the platform stack up (Postgres, Keycloak, OPA, Kafka) and the SUMO image

A. RE-RUN. Each live security verifier is run afresh, as a subprocess with the stack's environment, and must pass: identity (Keycloak
   realm and the authorization-code attacks, P09.02), the API's token enforcement matrix (P08.04), the policy engine (P09.03), workload
   database identities (P09.04), audit chain and redaction (P09.05), the data inventory against the live database (P09.06) and the
   access review of the live realm (P09.07). Run after everything built since they last ran, this is a regression check as much as an
   acceptance one.
B. COVERAGE. Every control of the catalogue is read for the evidence it cites. A check whose name says something is refused, denied,
   rejected, blocked or never happens is counted as a NEGATIVE check; the rest as positive. Each control that is implemented or partial
   and lacks either kind is listed as a finding with its owner and task. The classification is a reading of check names, stated as such,
   not a proof that the check is what its name says.
C. WHAT IS NOT COVERED is written into the evidence: the runtime detections on the target host (P09.09), transport inside a cluster
   (P11.04), and every control still planned.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402

ev = Evidence("P09.10", "p09_10_runtime_acceptance", docs_name="p09_10_runtime_acceptance")
ENV_FILE = SOURCE_ROOT / "infra" / "platform" / ".env"
CONTROLS = SOURCE_ROOT / "security" / "controls.json"
NEGATIVE = re.compile(
    r"refus|den(y|ied|ies)|reject|block|cannot|never|not_|_no_|no_|without|only|forbid|unauthori|invalid|tamper|forg|stale|expired|wrong|replay|"
    r"fail(s)?_closed|fail_closed|rate_limit|too_wide|missing|unknown|append_only|caught|detect|escalat|does_not|is_not|are_not|stays_quiet|quiet",
    re.I,
)
SWEEP = (
    (
        "identity: the Keycloak realm and the authorization-code attacks (P09.02)",
        "infra/platform/keycloak/verify_keycloak.py",
    ),
    ("API token enforcement matrix (P08.04)", "backend/api/verify_auth.py"),
    ("policy engine (P09.03)", "policy/verify_policy.py"),
    ("workload database identities (P09.04)", "database/verify_workload_protection.py"),
    ("audit hash chain, redaction and export (P09.05)", "backend/verify_audit_protection.py"),
    ("data inventory against the live database (P09.06)", "security/verify_data_inventory.py"),
    ("access review of the live realm (P09.07)", "security/access_review.py"),
)


def stack_env() -> dict[str, str]:
    env = dict(os.environ)
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env.setdefault(key.strip(), value.strip().strip("'\""))
    env.setdefault("POSTGRES_DB", "aiops")
    return env


def run_one(script: str, env: dict[str, str]) -> tuple[bool, str, float]:
    started = time.time()
    proc = subprocess.run(
        [sys.executable, str(SOURCE_ROOT / script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=3000,
        check=False,
    )
    out = (proc.stdout + proc.stderr).replace("\r", "")
    summary = next(
        (
            ln
            for ln in reversed(out.splitlines())
            if "CHECKS" in ln or "Traceback" in ln or "Error" in ln
        ),
        out.strip().splitlines()[-1] if out.strip() else "no output",
    )
    passes = len(re.findall(r"^PASS", out, re.M))
    fails = re.findall(r"^FAIL: (\S+)", out, re.M)
    ok = proc.returncode == 0 and "ALL CHECKS PASSED" in out or (proc.returncode == 0 and not fails)
    return (
        ok,
        f"{passes} checks passed, failing {fails[:3]}; {summary[:120]}",
        time.time() - started,
    )


def main() -> int:
    env = stack_env()
    results = {}
    for label, script in SWEEP:
        try:
            ok, detail, seconds = run_one(script, env)
        except subprocess.TimeoutExpired:
            ok, detail, seconds = False, "timed out", 3000.0
        results[script] = {"passed": ok, "seconds": round(seconds), "detail": detail}
        ev.check(f"re_running_{label}_passes", ok, f"{seconds:.0f} s; {detail}")
    ev.metrics["sweep"] = results

    # ---- B. coverage
    controls = json.loads(CONTROLS.read_text(encoding="utf-8"))["controls"]
    rows, findings = [], []
    for c in controls:
        names = [
            name
            for e in c.get("evidence", [])
            for name in [*e.get("checks", []), *e.get("tests", [])]
        ]
        pytest_files = [e["pytest"] for e in c.get("evidence", []) if "pytest" in e]
        negative = [n for n in names if NEGATIVE.search(n)]
        positive = [n for n in names if not NEGATIVE.search(n)]
        row = {
            "id": c["id"],
            "status": c["status"],
            "cited": len(c.get("evidence", [])),
            "checks": len(names),
            "pytest_files": len(pytest_files),
            "positive": len(positive),
            "negative": len(negative),
        }
        rows.append(row)
        if c["status"] in ("implemented", "partial") and (
            not names and not pytest_files or (names and (not positive or not negative))
        ):
            findings.append(
                {
                    "control": c["id"],
                    "status": c["status"],
                    "owner": c.get("owner"),
                    "task": c.get("task"),
                    "has_positive": bool(positive),
                    "has_negative": bool(negative),
                    "has_pytest": bool(pytest_files),
                }
            )
    live = [r for r in rows if r["status"] in ("implemented", "partial")]
    both = [r for r in live if r["positive"] and r["negative"]]
    ev.check(
        "every_finding_about_missing_positive_or_negative_evidence_names_an_owner_and_a_task",
        all(f["owner"] and f["task"] for f in findings),
        f"{len(both)} of {len(live)} implemented or partial controls cite both a positive and a negative runtime check; {len(findings)} findings",
    )
    ev.check(
        "no_implemented_control_has_no_evidence_at_all",
        all(r["cited"] for r in rows if r["status"] == "implemented"),
        f"{sum(1 for r in rows if r['status'] == 'implemented')} implemented controls",
    )
    ev.metrics["coverage"] = {
        "controls": len(rows),
        "implemented_or_partial": len(live),
        "with_positive_and_negative": len(both),
        "rows": rows,
    }
    ev.metrics["findings"] = findings
    ev.notes["not_covered"] = (
        "runtime detections on the target host (P09.09, the host was taken back by its owner), transport and identity inside a cluster (P11.04), "
        f"and the {sum(1 for r in rows if r['status'] == 'planned')} controls still planned; the positive/negative split reads check names and proves nothing about what a check does"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
