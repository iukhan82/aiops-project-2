"""Small shared recorder for the real-stack verify scripts: named pass/fail
checks plus optional measured-metrics payload, written as evidence JSON."""

from __future__ import annotations

import json
import time
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "infra" / "platform" / "output"
DOCS_EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence"


class Evidence:
    """`docs_name`, when given, also writes the same evidence to docs/evidence/<docs_name>.json, where the register links it."""

    def __init__(self, task: str, name: str | None = None, docs_name: str | None = None) -> None:
        self.task = task
        self.name = name or task.lower().replace(".", "_")
        self.docs_name = docs_name
        self.results: dict[str, bool] = {}
        self.notes: dict[str, str] = {}
        self.metrics: dict = {}

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results[name] = bool(ok)
        self.notes[name] = detail
        print(f"{'PASS' if ok else 'FAIL'}: {name} {detail}"[:400], flush=True)

    def finish(self) -> int:
        all_passed = all(self.results.values())
        payload = {
            "task": self.task,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "checks": self.results,
            "notes": self.notes,
            "metrics": self.metrics,
            "all_passed": all_passed,
        }
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / f"{self.name}_evidence.json"
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        print(f">> Evidence written to {path}")
        if self.docs_name:
            DOCS_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
            (DOCS_EVIDENCE_DIR / f"{self.docs_name}.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
            )
            print(f">> Evidence copied to docs/evidence/{self.docs_name}.json")
        print(f"{self.task}: {'ALL CHECKS PASSED' if all_passed else 'ONE OR MORE CHECKS FAILED'}")
        return 0 if all_passed else 1
