#!/usr/bin/env python3
"""P11.08 acceptance evidence: the runbook is followed AS WRITTEN, on the target, and it works.

    python3 runbook_drill.py PHASE [PHASE ...]        # ON the target host; phases: deploy, reconcile, recover, teardown

The runbook (`docs/runbooks/TARGET_DEPLOYMENT_RUNBOOK.md`) labels its command blocks ```bash step=NAME phase=PHASE [timeout=SECONDS]```. This script extracts those blocks
from the runbook file itself and runs each one, in the order they appear, in a fresh `bash`, with nothing added: no environment, no helper, no retry. Each block's exit status,
duration and last output lines are recorded, together with the SHA-256 of the runbook, so the evidence names the exact document that was followed.

What it proves: every command the runbook tells an operator to run exists, works and produces what the runbook says it will (a block that ends in a check prints the value the
prose promises, and this script compares it). What it does not prove: that a person who did not write the runbook could follow it - the drill has no judgement to fail on.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import time
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402

RUNBOOK = Path.home() / "aiops-p11" / "TARGET_DEPLOYMENT_RUNBOOK.md"
FENCE = re.compile(r"```bash (?P<attrs>[^\n]*)\n(?P<body>.*?)\n```", re.S)
# what a block must print, by step (the runbook's own prose promises these)
EXPECT = {
    "remove-previous": lambda out: out.strip().splitlines()[-1].strip() == "0",
    "reconcile-keeps-secrets": lambda out: "secrets unchanged by a second up" in out,
    "reconcile-corrects-drift": lambda out: out.strip().splitlines()[-1].strip() == "1",
    "disaster-empty-database": lambda out: out.strip().splitlines()[-1].strip() == "0",
    "verify-recovered": lambda out: (
        "every chained history is intact" in out and "1" in out.strip().splitlines()[-2]
    ),
    "teardown": lambda out: (
        out.strip().splitlines()[-1].strip() == "0" and out.strip().splitlines()[-2].strip() == "0"
    ),
    "verify-deployment": lambda out: "ALL CHECKS PASSED" in out,
    "verify-security": lambda out: "ALL CHECKS PASSED" in out,
}


def steps() -> list[dict]:
    text = RUNBOOK.read_text(encoding="utf-8")
    found = []
    for m in FENCE.finditer(text):
        attrs = dict(a.split("=", 1) for a in m.group("attrs").split() if "=" in a)
        if "step" in attrs:
            found.append(
                {
                    "step": attrs["step"],
                    "phase": attrs.get("phase", "deploy"),
                    "timeout": int(attrs.get("timeout", "300")),
                    "body": m.group("body"),
                }
            )
    return found


def main() -> int:
    phases = sys.argv[1:] or ["deploy"]
    # One evidence file per run of phases: the drill is run in pieces (the platform is exercised between them), and a later run must not overwrite an earlier one
    name = "p11_08_runbook_drill_" + "_".join(phases)
    ev = Evidence("P11.08", name, docs_name=name)
    text = RUNBOOK.read_text(encoding="utf-8")
    ev.notes["runbook_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    ev.notes["phases"] = ", ".join(phases)
    timings = {}
    for step in steps():
        if step["phase"] not in phases:
            continue
        started = time.time()
        try:
            proc = subprocess.run(
                ["bash", "-c", step["body"]],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # one stream, in the order the operator would see it: a check prints its answer LAST, and stderr must not follow it
                text=True,
                timeout=step["timeout"],
                check=False,
            )
            code, out = proc.returncode, proc.stdout
        except subprocess.TimeoutExpired as exc:
            code, out = (
                124,
                f"timed out after {step['timeout']} s\n{(exc.stdout or b'').decode('utf-8', 'replace')[-300:] if isinstance(exc.stdout, bytes) else ''}",
            )
        seconds = round(time.time() - started, 1)
        as_promised = EXPECT.get(step["step"], lambda _o: True)(out) if code == 0 else False
        timings[step["step"]] = {"phase": step["phase"], "seconds": seconds, "exit": code}
        tail = " | ".join(ln.strip() for ln in out.strip().splitlines()[-2:])[:200]
        ev.check(
            f"runbook_step_{step['step']}_runs_as_written_and_prints_what_the_runbook_promises",
            code == 0 and as_promised,
            f"{seconds} s, exit {code}; {tail}",
        )
    ev.metrics["step_seconds"] = timings
    ev.metrics["total_seconds"] = round(sum(t["seconds"] for t in timings.values()), 1)
    ev.notes["not_proven"] = (
        "a person other than the author reading it cold: this drill has no judgement to fail on"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
