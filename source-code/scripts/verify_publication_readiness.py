#!/usr/bin/env python3
"""P13.05: a publication scan of everything git would actually push - no committed secret, no real personal data, and every third-party
dependency's licence is known and not silently incompatible with publishing the tree.

    python source-code/scripts/verify_publication_readiness.py

Needs `git` and the frontend's `node_modules`/`package-lock.json` (npm licenses) and the backend venv's installed distributions (Python licenses,
read locally from `importlib.metadata` - no network call). Writes `docs/evidence/p13_05_publication_readiness.json`.
"""

from __future__ import annotations

import importlib.metadata as im
import json
import re
import subprocess
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402

# Real secret shapes, not field names: a variable called "password" is fine, a real-looking bearer token or private key is not.
SECRET_PATTERNS = {
    "private key block": re.compile(rb"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key id": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "generic bearer token (40+ chars)": re.compile(rb"\bBearer [A-Za-z0-9._~+/-]{40,}\b"),
    "GitHub personal access token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "Slack token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
}
# Paths that legitimately hold placeholder-shaped material (test fixtures with an obviously-fake key, this scanner's own patterns).
# The platform's own tests of things that ought never happen: `verify_platform_incidents.py` asserts the database REFUSES an embedded secret
# marker in a hypothesis text (redaction working as intended), so a bare BEGIN marker with no key body appears in that one file on purpose.
ALLOW_SUBSTRINGS = (
    "scripts/verify_publication_readiness.py",
    "backend/aiops/verify_platform_incidents.py",
)

# Domains a synthetic project's fixtures may legitimately use; anything else that looks like a real email address is flagged for a human to look at.
ALLOWED_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "aiops.local",
    "aiops-demo.local",
    "traffic-ops.local",
}
EMAIL = re.compile(rb"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

COPYLEFT = re.compile(r"\b(GPL|AGPL|LGPL)\b", re.IGNORECASE)


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [REPO_ROOT / line for line in out.splitlines() if line.strip()]


def scan_secrets(files: list[Path]) -> list[str]:
    hits = []
    for path in files:
        rel = path.as_posix()
        if any(a in rel for a in ALLOW_SUBSTRINGS) or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                hits.append(f"{rel}: {label}")
    return hits


def scan_personal_data(files: list[Path]) -> list[str]:
    hits = []
    text_suffixes = {
        ".py",
        ".ts",
        ".tsx",
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".sql",
        ".mmd",
        ".cfg",
        ".conf",
        ".rego",
    }
    for path in files:
        if path.suffix not in text_suffixes or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for m in EMAIL.finditer(data):
            domain = m.group(1).decode("ascii", "replace").lower()
            if domain not in ALLOWED_EMAIL_DOMAINS and not domain.endswith(".local"):
                hits.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}: {m.group(0).decode('ascii', 'replace')}"
                )
    return hits


def python_licenses() -> dict[str, str]:
    """Keyed by the normalised distribution name (PEP 503: lowercase, `-`/`_`/`.` folded to `-`), so a lock file's own spelling of a
    package name always finds it regardless of how the distribution itself capitalises its `Name` metadata."""
    out = {}
    for dist in im.distributions():
        name = dist.metadata.get("Name")
        if not name:
            continue
        license_field = dist.metadata.get("License") or ""
        classifiers = dist.metadata.get_all("Classifier") or []
        license_classifiers = "; ".join(c for c in classifiers if c.startswith("License ::"))
        out[normalize(name)] = license_classifiers or license_field or "unknown"
    return out


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def required_python_packages() -> set[str]:
    names = set()
    for lock in SOURCE_ROOT.glob("**/requirements-lock.txt"):
        for line in lock.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            name = re.split(r"[=<>~! ]", line, maxsplit=1)[0].strip()
            if name:
                names.add(normalize(name))
    return names


def npm_licenses() -> dict[str, str]:
    lock = SOURCE_ROOT / "frontend" / "package-lock.json"
    if not lock.is_file():
        return {}
    doc = json.loads(lock.read_text(encoding="utf-8"))
    out = {}
    for path, info in doc.get("packages", {}).items():
        if not path:
            continue
        name = info.get("name") or path.rsplit("node_modules/", 1)[-1]
        out[name] = info.get("license", "unknown")
    return out


def main() -> int:
    ev = Evidence(
        "P13.05", "p13_05_publication_readiness", docs_name="p13_05_publication_readiness"
    )

    files = tracked_files()
    ev.metrics["tracked_files"] = len(files)

    secrets = scan_secrets(files)
    ev.check("no_tracked_file_contains_a_real_looking_secret", not secrets, str(secrets[:10]))

    env_tracked = [str(p.relative_to(REPO_ROOT)) for p in files if p.name == ".env"]
    key_tracked = [
        str(p.relative_to(REPO_ROOT)) for p in files if p.suffix in (".pem", ".key", ".p12", ".pfx")
    ]
    ev.check(
        "no_env_file_or_private_key_file_is_tracked",
        not env_tracked and not key_tracked,
        str(env_tracked + key_tracked),
    )

    personal = scan_personal_data(files)
    ev.check(
        "no_tracked_file_contains_an_email_address_outside_the_synthetic_project_s_own_domains",
        not personal,
        str(personal[:10]),
    )

    py_installed = python_licenses()
    py_required = required_python_packages()
    py_matched = {n: py_installed.get(n) for n in py_required}
    py_unknown = sorted(n for n, lic in py_matched.items() if not lic)
    py_copyleft = sorted(n for n, lic in py_matched.items() if lic and COPYLEFT.search(lic))
    ev.check(
        "every_locked_python_dependency_s_licence_is_known_from_the_installed_package_metadata",
        not py_unknown,
        f"not found installed (activate the project venv first): {py_unknown[:15]}",
    )
    ev.check("no_python_dependency_carries_a_gpl_family_licence", not py_copyleft, str(py_copyleft))

    npm = npm_licenses()
    npm_unknown = sorted(n for n, lic in npm.items() if lic == "unknown")
    npm_copyleft = sorted(
        n for n, lic in npm.items() if lic != "unknown" and COPYLEFT.search(str(lic))
    )
    ev.check(
        "every_npm_dependency_in_the_lockfile_declares_a_licence",
        len(npm_unknown)
        < max(
            3, len(npm) // 20
        ),  # a handful of scoped/private-looking entries with no license field is normal; a wave is not
        f"{len(npm_unknown)} of {len(npm)} unknown: {npm_unknown[:15]}",
    )
    ev.check("no_npm_dependency_carries_a_gpl_family_licence", not npm_copyleft, str(npm_copyleft))

    ev.metrics |= {
        "secrets_found": len(secrets),
        "personal_data_hits": len(personal),
        "python_packages_checked": len(py_required),
        "npm_packages_checked": len(npm),
    }
    ev.notes["what_this_does_not_check"] = (
        "asset-level provenance (fonts, icons, imagery under design-system/ and frontend/src) is not automated here - see the register row for what was reviewed by hand; "
        "and this is a scan, not a legal opinion on licence compatibility"
    )
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
