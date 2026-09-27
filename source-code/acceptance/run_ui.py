#!/usr/bin/env python3
"""The operator console in a real browser, every role, against the real stack (P08.04-P08.10, re-run for P12.01).

    python source-code/acceptance/run_ui.py            # about 25 minutes; needs the platform stack, Keycloak provisioned, Chrome and node
    python source-code/acceptance/run_ui.py --only p08_08_ui_actions   # only the named evidence groups (the others' evidence is left as it is)

`--only` skips straight to the named groups, so it also skips the real wall-clock time the earlier groups spend running: acceptance.spec.ts's dispatch and incident-detail
screens need the demo feeder's emergency-call replay to have produced at least one call, which a full run gets for free. Use `--only` for groups with no such dependency
(auth, map, analytics, govern, actions); for incidents/dispatch or acceptance, run the whole thing.

It starts what the console needs - the demo world's live feeder, the command executor and the outcome verifier, then the command histories - runs the Playwright specs in real Chrome
(real Keycloak with Authorization Code and PKCE, the real API on its own port, the real scenario-control service; nothing mocked), turns each report into its evidence file with
`frontend/tools/e2e_evidence.py`, and stops what it started. The specs are the ones written in Phase 08; the API and the Vite server are started by Playwright's own configuration.

Exit status 0 only when every spec passed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = SOURCE_ROOT / "frontend"
PYTHON = sys.executable

# spec files (one Playwright invocation each) -> the evidence they become
GROUPS = (
    (("auth.spec.ts",), "P08.04", "p08_04_ui_auth"),
    (("map.spec.ts",), "P08.05", "p08_05_ui_map"),
    (("analytics.spec.ts",), "P08.06", "p08_06_ui_analytics"),
    (("incidents.spec.ts", "dispatch.spec.ts"), "P08.07", "p08_07_ui_incident_dispatch"),
    (("actions.spec.ts",), "P08.08", "p08_08_ui_actions"),
    (("govern.spec.ts",), "P08.09", "p08_09_ui_govern"),
    (
        ("acceptance.spec.ts", "failures.spec.ts", "latency.spec.ts"),
        "P08.10",
        "p08_10_ui_acceptance",
    ),
)


def start(argv: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        [PYTHON, *argv], cwd=SOURCE_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def main() -> int:
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    if npx is None:
        print("npx not found: node is needed for the browser specs")
        return 2
    only = sys.argv[sys.argv.index("--only") + 1 :] if "--only" in sys.argv else []
    env = {**os.environ, "CI": "1"}
    (FRONTEND / ".metrics").mkdir(exist_ok=True)
    if not only:
        for stale in (FRONTEND / ".metrics").glob("*.json"):
            stale.unlink()  # metrics are merged per section across specs; start from none
    feeder_log = SOURCE_ROOT / "infra" / "platform" / "output" / "run_ui_feeder.log"
    feeder_log.parent.mkdir(parents=True, exist_ok=True)
    feeder_log.write_text("", encoding="utf-8")
    with feeder_log.open("ab") as out:
        background = [
            subprocess.Popen(
                [PYTHON, "backend/demo/feeder.py", "--reset", "--start-offset-min", "60"],
                cwd=SOURCE_ROOT,
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        ]
    # The feeder prints once the demo database has been dropped and rebuilt. A fixed wait let the seed run before a slow reset, which then wiped it (seen once in P12.01).
    waited = 0
    while not feeder_log.read_text(encoding="utf-8", errors="replace").strip() and waited < 300:
        time.sleep(5)
        waited += 5
    time.sleep(15)  # and starts writing
    background += [
        start(["backend/control/executor_worker.py"]),
        start(["backend/control/verifier_worker.py", "--window-minutes", "5"]),
        start(
            ["backend/api/serve.py", "--port", "8100"]
        ),  # Playwright reuses these two instead of starting its own
        start(["backend/scenario_control/serve.py", "--port", "8101"]),
    ]
    time.sleep(15)
    seeded = subprocess.run(
        [PYTHON, "backend/demo/seed_actions.py"],
        cwd=SOURCE_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    print(f"seed_actions exit {seeded.returncode}", flush=True)
    if seeded.returncode != 0:
        print((seeded.stdout + seeded.stderr)[-600:], flush=True)
    failed = []
    try:
        for specs, task, name in GROUPS:
            if only and name not in only:
                continue
            print(f"RUN  {task} {name}: {', '.join(specs)}", flush=True)
            report = FRONTEND / "test-results" / "report.json"
            report.unlink(missing_ok=True)
            code = subprocess.run(
                [npx, "playwright", "test", *[f"e2e/{s}" for s in specs]],
                cwd=FRONTEND,
                env=env,
                check=False,
            ).returncode
            evidence = [PYTHON, "tools/e2e_evidence.py", "--task", task, "--name", name]
            if task == "P08.10" and (FRONTEND / ".metrics" / "p08_10.json").exists():
                evidence += ["--metrics", ".metrics/p08_10.json"]
            written = subprocess.run(
                evidence, cwd=FRONTEND, check=False, capture_output=True, text=True
            )
            print(
                f"     playwright exit {code}; {written.stdout.strip() or written.stderr.strip()[-200:]}",
                flush=True,
            )
            if code != 0 or written.returncode != 0:
                failed.append(name)
    finally:
        for proc in background:
            proc.terminate()
        for proc in background:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
    print(f"failed: {failed}" if failed else "every spec passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
