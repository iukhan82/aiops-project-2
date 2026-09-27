"""P01.08 acceptance evidence: the clean-start instructions really work in a clean environment.

    python source-code/scripts/verify_clean_start.py

`docs/environment/CLEAN_START.md` says: create a virtual environment, install `requirements-lock.txt`, run
`source-code/scripts/check.py`, and for the frontend `npm ci --ignore-scripts && npm run check`. This script does exactly
that in an environment that has never seen the repository - a fresh copy of the tree in a new directory, a NEW virtual
environment, and a clean `node:22` container for the frontend - and records what happened.

- Environment A: the working tree as it is (with the Markdown project documents), so the management validation runs.
- Environment B: the same tree with every Markdown file removed, the state of a checkout that does not store the
  documents, where the documents-dependent checks must skip rather than fail.

The copy excludes everything that is local state or secret (`.venv`, `node_modules`, generated `output/` directories,
certificates, keys, `.env`). On Windows the environments are inside WSL2 (Ubuntu), a genuinely different OS and Python
from the Windows development shell; on Linux they run locally.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402

ev = Evidence("P01.08", "p01_08_clean_start", docs_name="p01_08_clean_start")

EXCLUDES = (
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "output",
    "certs",
    "keys",
    ".env",
    "test-results",
    "playwright-report",
    ".scannerwork",
)


def _wsl_path(path: Path) -> str:
    text = str(path.resolve())
    return f"/mnt/{text[0].lower()}{text[2:].replace(chr(92), '/')}" if os.name == "nt" else text


def sh(script: str, timeout: float = 1800) -> subprocess.CompletedProcess:
    argv = ["wsl", "-e", "bash", "-lc", script] if os.name == "nt" else ["bash", "-lc", script]
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def clean_copy(target: str, drop_markdown: bool) -> None:
    excludes = " ".join(f"--exclude={shlex.quote(e)}" for e in EXCLUDES)
    script = (
        f"rm -rf {target} && mkdir -p {target} && cd {shlex.quote(_wsl_path(REPO_ROOT))} "
        f"&& tar cf - {excludes} . | tar xf - -C {target}"
    )
    if drop_markdown:
        script += f" && find {target} -name '*.md' -delete"
    result = sh(script)
    if result.returncode != 0:
        raise RuntimeError(f"copy failed: {result.stderr[-300:]}")


def python_smoke(target: str) -> tuple[bool, float, str]:
    started = time.time()
    script = (
        f"cd {target} && python3 --version && python3 -m venv .venv && . .venv/bin/activate "
        "&& python -m pip install --quiet --upgrade pip "
        "&& pip install --quiet -r source-code/requirements-lock.txt "
        "&& python source-code/scripts/check.py"
    )
    result = sh(script)
    return result.returncode == 0, time.time() - started, result.stdout + result.stderr


def main() -> int:
    versions = sh("python3 --version; . /etc/os-release && echo $PRETTY_NAME").stdout.split("\n")
    ev.metrics["environment"] = {"python": versions[0].strip(), "os": versions[1].strip()}

    for label, target, drop in (
        ("A_tree_with_markdown_documents", "/tmp/aiops-clean-a", False),
        ("B_tree_without_markdown_documents", "/tmp/aiops-clean-b", True),
    ):
        clean_copy(target, drop)
        ok, seconds, out = python_smoke(target)
        tail = "\n".join(out.strip().splitlines()[-6:])
        passed = re.search(r"(\d+) passed", out)
        skipped = re.search(r"(\d+) skipped", out)
        management = (
            "passed"
            if "Management validation passed" in out
            else "skipped"
            if "Management validation skipped" in out
            else "?"
        )
        ev.check(
            f"clean_environment_{label}_install_and_smoke_check_succeeds",
            ok,
            f"{seconds:.0f} s; pytest {passed and passed.group(1)} passed, {skipped and skipped.group(1) or 0} skipped; management {management}",
        )
        ev.metrics[label] = {
            "seconds": round(seconds),
            "pytest_passed": int(passed.group(1)) if passed else None,
            "pytest_skipped": int(skipped.group(1)) if skipped else 0,
            "management_validation": management,
            "tail": tail,
        }
        if not ok:
            print(out[-1500:])
    ev.check(
        "with_the_documents_present_the_management_validation_runs_and_passes",
        ev.metrics["A_tree_with_markdown_documents"]["management_validation"] == "passed",
    )
    ev.check(
        "without_the_documents_the_management_validation_skips_instead_of_failing",
        ev.metrics["B_tree_without_markdown_documents"]["management_validation"] == "skipped",
    )

    # Frontend: a clean node:22 container, the documented commands, on a fresh copy of the frontend sources only.
    front = "/tmp/aiops-clean-a/source-code/frontend"
    started = time.time()
    result = sh(
        f"docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -v {front}:/w -w /w node:22-alpine "
        "sh -c 'node --version && npm ci --ignore-scripts --no-audit --no-fund && npm run check'"
    )
    out = re.sub(r"\[[0-9;]*m", "", result.stdout + result.stderr)  # vitest colours its summary
    tests = re.search(r"Tests\s+(\d+) passed", out)
    ev.check(
        "frontend_npm_ci_and_check_succeed_in_a_clean_node_22_container",
        result.returncode == 0 and bool(tests),
        f"{time.time() - started:.0f} s; vitest {tests.group(1) if tests else '?'} tests passed",
    )
    if result.returncode != 0 or not tests:
        print(out[-2500:].encode("ascii", "replace").decode())
    for target in ("/tmp/aiops-clean-a", "/tmp/aiops-clean-b"):
        sh(f"rm -rf {target}")
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
