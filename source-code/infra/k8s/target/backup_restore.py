#!/usr/bin/env python3
"""P11.05: encrypted backup and restore of the platform database. Runs ON the target host (kubectl, openssl).

    backup_restore.py backup [DIR]            dump the roles and the `aiops` database, encrypt both, write a manifest with sizes and hashes
    backup_restore.py restore DIR [DATABASE]  decrypt and restore into DATABASE (default `aiops`; a scratch name is used to TEST a backup)

What a backup holds: `globals.sql.enc` (the roles and their password hashes - `pg_dump` alone does not carry them, and without them the
workload roles do not exist after a restore) and `aiops.dump.enc` (`pg_dump -Fc`, the whole database including the PostGIS extension). Both are
AES-256-CBC with a random key and PBKDF2, the key generated once into `DIR/.key` (mode 0600, DIR mode 0700): whoever can read the directory as
its owner can restore it, and nobody else. Keep the key apart from the archive in any real use; here they share a directory on one host, which
is a stated limit, not a design.

The dump is taken from the running database (`pg_dump` is a consistent snapshot); a caller that needs a quiet database - a test that compares
fingerprints - quiesces the writers first. Nothing is written into the cluster except the scratch database of a test restore.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

NS = "aiops"
DEFAULT_DIR = Path.home() / "aiops-p11" / "backup"


def kubectl(
    *args: str, stdin: bytes | None = None, timeout: int = 900
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", *args], capture_output=True, input=stdin, timeout=timeout, check=False
    )


def postgres_user() -> str:
    out = kubectl(
        "-n", NS, "get", "secret", "aiops-secrets", "-o", "jsonpath={.data.postgres-user}"
    )
    return base64.b64decode(out.stdout).decode()


def key_file(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    key = directory / ".key"
    if not key.exists():
        key.write_bytes(base64.b64encode(os.urandom(48)))
        key.chmod(0o600)
    return key


def encrypt(data: bytes, key: Path) -> bytes:
    done = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", f"file:{key}"],
        input=data,
        capture_output=True,
        check=True,
    )
    return done.stdout


def decrypt(data: bytes, key: Path) -> bytes:
    done = subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", f"file:{key}"],
        input=data,
        capture_output=True,
        check=True,
    )
    return done.stdout


def backup(directory: Path = DEFAULT_DIR) -> dict:
    started = time.time()
    user, key = postgres_user(), key_file(directory)
    roles = kubectl(
        "-n", NS, "exec", "sts/aiops-postgres", "--", "pg_dumpall", "-U", user, "--globals-only"
    )
    dump = kubectl(
        "-n", NS, "exec", "sts/aiops-postgres", "--", "pg_dump", "-U", user, "-Fc", "aiops"
    )
    if roles.returncode or dump.returncode or not dump.stdout:
        raise RuntimeError(f"dump failed: {(roles.stderr + dump.stderr).decode()[-300:]}")
    files = {}
    for name, data in (("globals.sql.enc", roles.stdout), ("aiops.dump.enc", dump.stdout)):
        sealed = encrypt(data, key)
        (directory / name).write_bytes(sealed)
        (directory / name).chmod(0o600)
        files[name] = {
            "bytes": len(sealed),
            "sha256": hashlib.sha256(sealed).hexdigest(),
            "plain_bytes": len(data),
        }
    manifest = {
        "taken_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.time() - started, 2),
        "files": files,
        "cipher": "aes-256-cbc pbkdf2",
        "format": "pg_dump -Fc + pg_dumpall --globals-only",
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def verify_files(directory: Path) -> bool:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    return all(
        hashlib.sha256((directory / n).read_bytes()).hexdigest() == m["sha256"]
        for n, m in manifest["files"].items()
    )


def restore(directory: Path, database: str = "aiops", with_globals: bool = True) -> dict:
    started = time.time()
    user, key = postgres_user(), key_file(directory)
    if not verify_files(directory):
        raise RuntimeError(
            "the backup does not match its manifest: refusing to restore a file that changed"
        )
    steps = {}
    if with_globals:
        # roles that already exist (the bootstrap user) are reported as errors and skipped: that is expected on a fresh instance
        globals_sql = decrypt((directory / "globals.sql.enc").read_bytes(), key)
        done = kubectl(
            "-n",
            NS,
            "exec",
            "-i",
            "sts/aiops-postgres",
            "--",
            "psql",
            "-U",
            user,
            "-d",
            "postgres",
            "-q",
            stdin=globals_sql,
        )
        steps["roles"] = done.returncode == 0
    kubectl(
        "-n",
        NS,
        "exec",
        "sts/aiops-postgres",
        "--",
        "psql",
        "-U",
        user,
        "-d",
        "postgres",
        "-c",
        f'DROP DATABASE IF EXISTS "{database}"',
    )
    created = kubectl(
        "-n",
        NS,
        "exec",
        "sts/aiops-postgres",
        "--",
        "psql",
        "-U",
        user,
        "-d",
        "postgres",
        "-c",
        f'CREATE DATABASE "{database}"',
    )
    steps["database_created"] = created.returncode == 0
    dump = decrypt((directory / "aiops.dump.enc").read_bytes(), key)
    restored = kubectl(
        "-n",
        NS,
        "exec",
        "-i",
        "sts/aiops-postgres",
        "--",
        "pg_restore",
        "-U",
        user,
        "-d",
        database,
        "--no-owner",
        "--role",
        user,
        stdin=dump,
    )
    steps["restored"] = restored.returncode == 0
    steps["pg_restore_messages"] = restored.stderr.decode()[-300:]
    steps["seconds"] = round(time.time() - started, 2)
    return steps


def main() -> int:
    command = sys.argv[1]
    directory = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DIR
    if command == "backup":
        print(json.dumps(backup(directory), indent=2))
    elif command == "restore":
        print(
            json.dumps(restore(directory, sys.argv[3] if len(sys.argv) > 3 else "aiops"), indent=2)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
