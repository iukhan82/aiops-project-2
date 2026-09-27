#!/usr/bin/env python3
"""Run the platform's verifiers one after another against the live stack and record what happened (P12.01, P12.06).

    python source-code/acceptance/run_catalog.py --list
    python source-code/acceptance/run_catalog.py --group offline stack           # a slice, in catalog order
    python source-code/acceptance/run_catalog.py --run ingestion gateway         # named runs
    python source-code/acceptance/run_catalog.py --all --stale-before 2026-09-26  # everything whose evidence is older than the date

Each run is a separate process with the stack's environment (`infra/platform/.env`, never printed) and `POSTGRES_DB=aiops`. A run passes when it exits 0. The
evidence files it changed are then read, and any failing check is listed. Results accumulate in `infra/platform/output/acceptance_runs.json`, one record per run id
(the latest), so a slice can be re-run without losing the rest; `build_bundle.py` (P12.06) turns them into the evidence index.

The verifiers share one database and one set of containers, so they are never run in parallel, and a failure does not stop the sequence unless `--stop-on-fail` is given: the
point is to learn what is broken, all of it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
EVIDENCE = REPO_ROOT / "docs" / "evidence"
RESULTS = SOURCE_ROOT / "infra" / "platform" / "output" / "acceptance_runs.json"
ENV_FILE = SOURCE_ROOT / "infra" / "platform" / ".env"
sys.path.insert(0, str(SOURCE_ROOT))

from acceptance.runs import OUTPUT_OF, RUNS, Run  # noqa: E402

OUTPUT = SOURCE_ROOT / "infra" / "platform" / "output"


def stack_environment() -> dict[str, str]:
    """The process environment plus the stack's own (parsed, never echoed) and the database every verifier expects."""
    env = dict(os.environ)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    env["POSTGRES_DB"] = "aiops"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def snapshot() -> dict[str, tuple[int, int]]:
    return {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in EVIDENCE.glob("*.json")}


def output_snapshot() -> dict[str, tuple[int, int]]:
    return {
        p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in OUTPUT.glob("*_evidence.json")
    }


def output_name(run: Run, docs_name: str, index: int) -> str | None:
    """The file in `infra/platform/output` this run's verifier writes for the docs evidence `docs_name`."""
    if docs_name in OUTPUT_OF:
        return OUTPUT_OF[docs_name]
    return f"{run.task.lower().replace('.', '_')}_evidence.json" if index == 0 else None


def refresh_docs(run: Run, before: dict[str, tuple[int, int]]) -> list[str]:
    """Copy what the run just wrote in the output directory to docs/evidence, where the register links it. Returns what was copied."""
    copied = []
    after = output_snapshot()
    for index, docs_name in enumerate(run.evidence):
        out = output_name(run, docs_name, index)
        if (
            out
            and out in after
            and before.get(out) != after[out]
            and not (EVIDENCE / f"{docs_name}.json").is_symlink()
        ):
            (EVIDENCE / f"{docs_name}.json").write_bytes((OUTPUT / out).read_bytes())
            copied.append(f"{out} -> docs/evidence/{docs_name}.json")
    return copied


def read_evidence(name: str) -> dict:
    path = EVIDENCE / name
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"file": name, "readable": False}
    checks = doc.get("checks")
    failing = [k for k, v in checks.items() if v is False] if isinstance(checks, dict) else []
    passed = doc.get("all_passed")
    if passed is None and "tests_passed" in doc:
        passed = doc.get("tests_passed") == doc.get("tests_total")
    return {
        "file": name,
        "readable": True,
        "all_passed": passed,
        "checks": len(checks) if isinstance(checks, dict) else doc.get("tests_total"),
        "failing": failing[:12],
        "generated_at": doc.get("generated_at"),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def run_one(run: Run, env: dict[str, str], timeout_scale: float) -> dict:
    argv = [sys.executable, *run.argv]
    before = snapshot()
    output_before = output_snapshot()
    started = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        proc = subprocess.run(
            argv,
            cwd=SOURCE_ROOT / run.cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=run.minutes * 60 * timeout_scale,
            check=False,
        )
        code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        code = 124
        out = f"timed out after {run.minutes * timeout_scale} minutes\n" + (
            (exc.stdout or b"").decode("utf-8", "replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
    seconds = round(time.time() - started, 1)
    copied = refresh_docs(run, output_before)
    after = snapshot()
    changed = sorted(n for n, sig in after.items() if before.get(n) != sig)
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    evidence = [
        read_evidence(n)
        for n in dict.fromkeys(
            [*[f"{e}.json" for e in run.evidence if f"{e}.json" in changed], *changed]
        )
    ]
    return {
        "id": run.id,
        "task": run.task,
        "title": run.title,
        "group": run.group,
        "command": "python " + " ".join(run.argv),
        "started": stamp,
        "seconds": seconds,
        "exit": code,
        "passed": code == 0,
        "evidence_written": evidence,
        "copied_from_output": copied,
        "evidence_expected_but_not_written": [
            f"{e}.json" for e in run.evidence if f"{e}.json" not in changed
        ],
        "output_tail": lines[-8:],
    }


def quiesce(env: dict[str, str]) -> dict[str, int]:
    """Put the shared verification database back to quiet between two runs.

    The verifiers share one database (`aiops`) and several leave their fixtures behind: a live collision incident closes a segment for the routing check that runs after it, a
    command nobody executed is picked up by the next executor. The database holds nothing but verification fixtures (the demo world lives in `aiops_demo`), so between runs every
    live incident is resolved and every command still waiting is expired through the repository, and each run starts from a platform with nothing going on.
    """
    import psycopg  # noqa: PLC0415

    os.environ.update({k: v for k, v in env.items() if k.startswith(("POSTGRES_", "PG"))})
    from backend.repositories import commands as command_repo  # noqa: PLC0415
    from database.migrate import dsn_from_env  # noqa: PLC0415

    far_future = datetime.now(UTC) + timedelta(days=3650)
    with psycopg.connect(dsn_from_env()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE incidents SET status = 'resolved', resolved_at = now(), updated_at = now() WHERE status IN ('open', 'acknowledged', 'investigating', 'escalated', 'reopened')"
            )
            resolved = cur.rowcount
        conn.commit()
        expired = command_repo.expire_stale(conn, far_future)
        conn.commit()
    return {"incidents_resolved": resolved, "commands_expired": expired}


def sync_docs() -> list[str]:
    """Copy every verifier output that is newer than its docs/evidence copy (for runs made before the runner did this itself)."""
    copied = []
    for run in RUNS:
        for index, docs_name in enumerate(run.evidence):
            out = output_name(run, docs_name, index)
            source, target = OUTPUT / (out or "-"), EVIDENCE / f"{docs_name}.json"
            if (
                out
                and source.exists()
                and (not target.exists() or source.stat().st_mtime > target.stat().st_mtime)
            ):
                target.write_bytes(source.read_bytes())
                copied.append(f"{out} -> docs/evidence/{docs_name}.json")
    return copied


def load_results() -> dict[str, dict]:
    if RESULTS.exists():
        return {r["id"]: r for r in json.loads(RESULTS.read_text(encoding="utf-8"))["runs"]}
    return {}


def save_results(results: dict[str, dict]) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    order = {r.id: i for i, r in enumerate(RUNS)}
    RESULTS.write_text(
        json.dumps(
            {"runs": sorted(results.values(), key=lambda r: order.get(r["id"], 999))}, indent=2
        ),
        encoding="utf-8",
    )


def evidence_generated_at(name: str) -> str:
    try:
        return str(
            json.loads((EVIDENCE / f"{name}.json").read_text(encoding="utf-8")).get(
                "generated_at", ""
            )
        )
    except (OSError, ValueError):
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--group", nargs="*", default=[])
    parser.add_argument("--run", nargs="*", default=[])
    parser.add_argument(
        "--stale-before",
        help="skip a run whose expected evidence was all generated on or after this date (YYYY-MM-DD)",
    )
    parser.add_argument("--stop-on-fail", action="store_true")
    parser.add_argument(
        "--sync-docs",
        action="store_true",
        help="refresh docs/evidence from newer verifier outputs and exit",
    )
    parser.add_argument("--timeout-scale", type=float, default=1.5)
    args = parser.parse_args()

    if args.sync_docs:
        for line in sync_docs():
            print(line)
        return 0
    if args.list:
        for r in RUNS:
            print(
                f"{r.id:22} {r.task}  {r.group:10} ~{r.minutes:>3} min  {' '.join(r.needs):20} {r.title}"
            )
        print(
            f"{len(RUNS)} runs, about {sum(r.minutes for r in RUNS) / 60:.1f} hours if every estimate is met"
        )
        return 0
    chosen = [r for r in RUNS if args.all or r.group in args.group or r.id in args.run]
    if not chosen:
        parser.error("choose --all, --group or --run")
    env = stack_environment()
    results = load_results()
    failed = 0
    for run in chosen:
        if (
            args.stale_before
            and run.evidence
            and all(evidence_generated_at(e)[:10] >= args.stale_before for e in run.evidence)
        ):
            print(f"SKIP {run.id}: its evidence is from {args.stale_before} or later", flush=True)
            continue
        if "stack" in run.needs:
            print(f"     quiet: {quiesce(env)}", flush=True)
        print(f"RUN  {run.id} ({run.task}, ~{run.minutes} min) ...", flush=True)
        result = run_one(run, env, args.timeout_scale)
        results[run.id] = result
        save_results(results)
        bad = [e for e in result["evidence_written"] if e.get("all_passed") is False]
        print(
            f"{'PASS' if result['passed'] else 'FAIL'} {run.id} in {result['seconds']} s; evidence: {[e['file'] for e in result['evidence_written']]}"
            + (f"; FAILING {[(e['file'], e['failing']) for e in bad]}" if bad else ""),
            flush=True,
        )
        if not result["passed"]:
            failed += 1
            print("     " + " | ".join(result["output_tail"][-3:])[:400], flush=True)
            if args.stop_on_fail:
                break
    print(f"{len(chosen)} chosen, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
