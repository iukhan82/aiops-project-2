"""P11.01: the pinned dependency closure of the platform's SERVICE images (`backend/requirements-lock.txt`).

    python source-code/scripts/build_service_lock.py            # rewrite the file
    python source-code/scripts/build_service_lock.py --check    # fail if it is not what the environment and the root lock produce

The root `requirements-lock.txt` is the whole development environment (tests, plotting, model training, scanners). A service image needs
a small part of it, and every package in an image is attack surface and scan noise. This script starts from the third-party packages
the service code (`backend/`, `database/`, the operational detector's runtime) actually imports, walks their declared
dependencies for a Linux, Python 3.12 target, and pins every one at the version the root lock already pins - so there is one version of
each package in the project, and the service closure is a subset of the reviewed lock, never a second resolution.

`--check` also fails when a package the service closure needs is not in the root lock, or is pinned differently, or when the service
code imports a third-party package that is in neither the roots below nor their closure.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

SOURCE_ROOT = Path(__file__).resolve().parents[1]
ROOT_LOCK = SOURCE_ROOT / "requirements-lock.txt"
OUTPUT = SOURCE_ROOT / "backend" / "requirements-lock.txt"

# import name -> distribution name, for the third-party packages the service code imports
IMPORT_TO_DIST = {
    "confluent_kafka": "confluent-kafka",
    "cryptography": "cryptography",
    "fastapi": "fastapi",
    "httpx": "httpx",
    "joblib": "joblib",
    "jsonschema": "jsonschema",
    "jwt": "PyJWT",
    "numpy": "numpy",
    "opentelemetry": "opentelemetry-sdk",
    "paho": "paho-mqtt",
    "psycopg": "psycopg-binary",
    "pydantic": "pydantic",
    "requests": "requests",
    "scipy": "scipy",
    "sklearn": "scikit-learn",
    "starlette": "starlette",
    "uvicorn": "uvicorn",
}
EXTRA_ROOTS = (
    "psycopg",
    "opentelemetry-exporter-otlp-proto-http",
)  # imported through a plugin or a companion package
SERVICE_CODE = ("backend", "database", "models/operations_detector")
NOT_SERVICE = re.compile(
    r"(^|/)(verify_|tests?/|train_|evaluate_)|build_operations_dataset|capture_run|/load\.py$"
)
LOCAL = {
    "backend",
    "database",
    "models",
    "edge",
    "policy",
    "contracts",
    "simulator",
    "security",
    "infra",
    "scripts",
    "tests",
}
LINUX_312 = {
    "os_name": "posix",
    "sys_platform": "linux",
    "platform_system": "Linux",
    "platform_machine": "x86_64",
    "platform_python_implementation": "CPython",
    "python_version": "3.12",
    "python_full_version": "3.12.0",
    "implementation_name": "cpython",
    "extra": "",
}


def root_pins() -> dict[str, str]:
    pins = {}
    for line in ROOT_LOCK.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==(\S+)", line.strip())
        if m:
            pins[canonicalize_name(m.group(1))] = m.group(2)
    return pins


def service_imports() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for base in SERVICE_CODE:
        for path in (SOURCE_ROOT / base).rglob("*.py"):
            rel = path.relative_to(SOURCE_ROOT).as_posix()
            if NOT_SERVICE.search(rel) or "__pycache__" in rel:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module.split(".")[0]]
                for name in names:
                    if name not in sys.stdlib_module_names and name not in LOCAL:
                        found.setdefault(name, set()).add(rel)
    return found


def closure(roots: list[str]) -> tuple[set[str], list[str]]:
    seen: set[str] = set()
    missing: list[str] = []
    stack = [canonicalize_name(r) for r in roots]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        seen.add(name)
        for text in dist.requires or []:
            req = Requirement(text)
            if req.marker is not None and not req.marker.evaluate(LINUX_312):
                continue
            stack.append(canonicalize_name(req.name))
    return seen, missing


def build() -> tuple[str, list[str]]:
    problems: list[str] = []
    imports = service_imports()
    unknown = sorted(set(imports) - set(IMPORT_TO_DIST))
    if unknown:
        problems.append(f"service code imports packages this script does not know: {unknown}")
    roots = sorted({IMPORT_TO_DIST[i] for i in imports if i in IMPORT_TO_DIST} | set(EXTRA_ROOTS))
    names, missing = closure(roots)
    pins = root_pins()
    lines = []
    for name in sorted(names):
        if name not in pins:
            problems.append(f"{name} is needed by the services but is not in the root lock")
            continue
        installed = metadata.version(name)
        if installed != pins[name]:
            problems.append(f"{name}: root lock pins {pins[name]}, the environment has {installed}")
        lines.append(f"{name}=={pins[name]}")
    if missing:
        problems.append(
            f"declared dependencies not installed here (needed on Linux?): {sorted(set(missing))}"
        )
    header = (
        "# P11.01: pinned closure of the service images, generated by source-code/scripts/build_service_lock.py from\n"
        "# the imports of backend/, database/ and the operational detector's runtime, pinned at the root lock's versions.\n"
    )
    return header + "\n".join(lines) + "\n", problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text, problems = build()
    for p in problems:
        print(f"problem: {p}", file=sys.stderr)
    if args.check:
        current = (
            OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n") if OUTPUT.is_file() else ""
        )
        if problems or current != text:
            print(
                f"{OUTPUT} is not what the environment and the root lock produce", file=sys.stderr
            )
            return 1
        print("service lock is current")
        return 0
    if problems:
        return 1
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(
        f"wrote {OUTPUT} ({len(text.splitlines()) - 2} packages, root lock has {len(root_pins())})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
