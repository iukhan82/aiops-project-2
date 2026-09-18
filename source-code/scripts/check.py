"""Run the repository's platform-independent development checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run(*args: str) -> None:
    print(f"+ {' '.join(args)}", flush=True)
    subprocess.run([sys.executable, "-m", *args], cwd=ROOT, check=True)


def main() -> int:
    run("ruff", "format", "--check", "source-code")
    run("ruff", "check", "source-code")
    run("pytest", "-q")
    subprocess.run(
        [sys.executable, "source-code/scripts/validate_management.py"],
        cwd=ROOT,
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
