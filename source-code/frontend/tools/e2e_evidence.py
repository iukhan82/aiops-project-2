"""Turn the Playwright JSON report into an evidence file.

    python source-code/frontend/tools/e2e_evidence.py --task P08.05 --name p08_05_ui_map [--metrics metrics.json]

Run it straight after `npm run e2e -- e2e/<spec>.ts`: the report holds only the last
run. The evidence lists every test with its outcome and duration, the browser and
viewport, and any measured metrics a spec attached (for example LAT-05). A failing
or skipped test makes the evidence say so; nothing is filtered out. `--rerun-of` merges an
earlier report of the same suite and lists every test that failed first and passed on a rerun,
with the first error, so a rerun after an environment fault stays visible.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1]
REPO_ROOT = FRONTEND.parents[1]
REPORT = FRONTEND / "test-results" / "report.json"
EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"


def collect(suite: dict, prefix: str = "") -> list[dict]:
    rows = []
    title = (
        f"{prefix} > {suite['title']}"
        if prefix and suite.get("title")
        else suite.get("title", prefix)
    )
    for spec in suite.get("specs", []):
        result = spec["tests"][0]["results"][-1]
        rows.append(
            {
                "title": f"{title} > {spec['title']}" if title else spec["title"],
                "file": spec.get("file"),
                "status": result["status"],
                "duration_ms": result["duration"],
                "error": (result.get("error") or {}).get("message", "")[:300] or None,
            }
        )
    for child in suite.get("suites", []):
        rows += collect(child, title)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--metrics", help="JSON file of measured metrics to include")
    parser.add_argument(
        "--rerun-of",
        action="append",
        default=[],
        help="an earlier report.json of the same suite (repeatable); its tests are kept, and a test that was run again later is replaced by the newer result with the first failure recorded",
    )
    args = parser.parse_args()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    rows = []
    for suite in report["suites"]:
        rows += collect(suite)
    retried = []
    if args.rerun_of:
        # Oldest first; the report just written is the newest. A later result for the same test replaces an earlier one.
        by_title: dict[str, dict] = {}
        order: list[str] = []
        for path in [*args.rerun_of, None]:
            source = (
                rows
                if path is None
                else [
                    r
                    for suite in json.loads(Path(path).read_text(encoding="utf-8"))["suites"]
                    for r in collect(suite)
                ]
            )
            for row in source:
                before = by_title.get(row["title"])
                if before is None:
                    order.append(row["title"])
                elif before["status"] != "passed" and row["status"] == "passed":
                    retried.append(
                        {
                            "title": row["title"],
                            "first_attempt": before["status"],
                            "first_error": before["error"],
                            "later_attempt": row["status"],
                        }
                    )
                by_title[row["title"]] = row
        rows = [by_title[title] for title in order]
    passed = sum(1 for r in rows if r["status"] == "passed")
    config = report.get("config", {}).get("projects", [{}])[0].get("use", {})
    evidence = {
        "task": args.task,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "browser": {
            "channel": config.get("channel", "chrome"),
            "headless": config.get("headless", True),
            "viewport": config.get("viewport"),
        },
        "stack": "real Chrome, real Keycloak (Authorization Code + PKCE), real API on the demo database fed by backend/demo/feeder.py",
        "tests_total": len(rows),
        "tests_passed": passed,
        "all_passed": passed == len(rows) and len(rows) > 0,
        "retried_after_a_failed_first_attempt": retried,
        "tests": rows,
        "metrics": json.loads(Path(args.metrics).read_text(encoding="utf-8"))
        if args.metrics
        else {},
    }
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE_DIR / f"{args.name}.json"
    out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"{args.task}: {passed}/{len(rows)} passed -> {out}")
    return 0 if evidence["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
