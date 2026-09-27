"""P11.03: pack the source tree for the target host, leaving out everything local, generated or secret.

    python source-code/infra/k8s/target/pack_source.py OUT.tar.gz

The host builds the platform images from this archive (the same Dockerfiles and the same reproducible-build flags as the workstation), so the
digest it produces can be compared with the workstation's: the same inputs must give the same image on a different machine. Certificates, keys,
`.env` files, virtual environments, `node_modules`, generated `output/` directories and the .git history never leave the workstation.
"""

from __future__ import annotations

import fnmatch
import io
import json
import sys
import tarfile
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[3]
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "output",
    "certs",
    "keys",
    "dist",
    "test-results",
    "playwright-report",
    ".scannerwork",
}
EXCLUDE_FILES = ("*.pyc", ".env", ".env.*", "*.key", "*.log", "*.sqlite", "*.md")
KEEP_ENV_EXAMPLE = ".env.example"


def wanted(path: Path) -> bool:
    relative = path.relative_to(SOURCE_ROOT)
    if any(part in EXCLUDE_DIRS for part in relative.parts[:-1]):
        return False
    name = path.name
    if name == KEEP_ENV_EXAMPLE:
        return True
    return not any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDE_FILES)


def edge_site_files() -> dict[str, bytes]:
    """The one edge site's inputs, from generated simulator output that a checkout does not carry: the device registry and the loop
    detectors' recorded ten-minute run. Small enough for a ConfigMap; the site runs them at one hundredth of real time."""
    run = SOURCE_ROOT / "simulator" / "sensors" / "output" / "run-a"
    loops = [
        ln
        for ln in (run / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        if '"traffic.loop_detector.count"' in ln
    ]
    config = {
        "site_id": "site-a", "runtime_device_id": "edge-runtime-site-a", "geometry_version": "2026-09-18.1", "boot_id": "site-a-1",
        "registry_path": "/site/devices.jsonl", "baseline_path": "/site/baseline_v1.json", "model_dir": "/model",
    }  # fmt: skip
    return {
        "edge-site/devices.jsonl": (run / "devices.jsonl").read_bytes(),
        "edge-site/events.jsonl": ("\n".join(loops) + "\n").encode("utf-8"),
        "edge-site/config.json": (json.dumps(config, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    }


def signed_digests() -> dict[str, bytes]:
    """The image digests the workstation's build signed (P11.01 evidence), so the target can compare what it runs with them."""
    evidence = json.loads(
        (SOURCE_ROOT.parent / "docs" / "evidence" / "p11_01_service_images.json").read_text(
            encoding="utf-8"
        )
    )
    if not evidence.get("all_passed"):
        raise SystemExit("the P11.01 evidence does not pass: there is no signed image to ship")
    digests = {name: evidence["metrics"][name]["digest"] for name in ("backend", "frontend")}
    return {
        "signed/service_images.json": (json.dumps(digests, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
    }


def main() -> int:
    out = Path(sys.argv[1])
    count = 0
    with tarfile.open(out, "w:gz") as tar:
        for name, data in {**edge_site_files(), **signed_digests()}.items():
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), 0
            tar.addfile(info, io.BytesIO(data))
            count += 1
        for path in sorted(SOURCE_ROOT.rglob("*")):
            if path.is_file() and wanted(path):
                info = tar.gettarinfo(
                    str(path), arcname=f"source-code/{path.relative_to(SOURCE_ROOT).as_posix()}"
                )
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = int(path.stat().st_mtime)
                with path.open("rb") as handle:
                    tar.addfile(info, handle)
                count += 1
    print(f"{count} files, {out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
