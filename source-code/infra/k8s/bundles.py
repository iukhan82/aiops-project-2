"""P11.03: the configuration files the platform's workloads read, as Kubernetes ConfigMaps.

The compose stack bind-mounts these from the repository; a cluster has no repository, so `deploy.py` creates one ConfigMap per bundle here
and `generate_workloads.py` mounts it. Both import this module, so the keys a ConfigMap carries and the paths the pod sees cannot disagree.

A ConfigMap key cannot contain `/`, so a file in a sub-directory is stored under its path with `/` written `__` and mounted back to its real
path with `items`. The compose stack names its neighbours by their short service names (`tempo`, `loki`, `prometheus`); the cluster names
them `aiops-tempo` and so on, so those hosts are rewritten on the way in - nothing else in a file is changed.
"""

from __future__ import annotations

import gzip
import io
import re
import tarfile
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
PLATFORM = SOURCE_ROOT / "infra" / "platform"
TARGET = Path(__file__).resolve().parent / "target"
HOSTS = {"prometheus": "aiops-prometheus", "tempo": "aiops-tempo", "loki": "aiops-loki"}
_HOST = re.compile(r"(?<![\w./-])(" + "|".join(HOSTS) + r"):(\d+)")


def rewrite_hosts(text: str) -> str:
    return _HOST.sub(lambda m: f"{HOSTS[m.group(1)]}:{m.group(2)}", text)


def _key(path: str) -> str:
    return path.replace("/", "__")


def _files() -> dict[str, dict[str, tuple[Path, bool]]]:
    """bundle -> {path inside the pod: (source file, rewrite hosts)}"""
    policy = SOURCE_ROOT / "policy"
    policy_files = {
        p.relative_to(policy).as_posix(): (p, False)
        for p in sorted(policy.rglob("*"))
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix in (".rego", ".json", "")
        and not p.name.endswith("_test.rego")
        and (p.suffix in (".rego", ".json") or p.name == ".manifest")
    }
    prom = PLATFORM / "observability" / "prometheus"
    grafana = PLATFORM / "observability" / "grafana"
    return {
        "aiops-policy": policy_files,
        "aiops-prometheus": {
            "prometheus.yml": (prom / "prometheus.yml", True),
            **{f"rules/{p.name}": (p, False) for p in sorted((prom / "rules").glob("*.yml"))},
        },
        "aiops-tempo": {"tempo.yaml": (PLATFORM / "observability" / "tempo" / "tempo.yaml", True)},
        "aiops-loki": {"loki.yaml": (PLATFORM / "observability" / "loki" / "loki.yaml", True)},
        "aiops-grafana-provisioning": {
            "datasources/datasources.yaml": (
                grafana / "provisioning" / "datasources" / "datasources.yaml",
                True,
            ),
            "dashboards/dashboards.yaml": (
                grafana / "provisioning" / "dashboards" / "dashboards.yaml",
                False,
            ),
        },
        "aiops-grafana-dashboards": {
            p.name: (p, False) for p in sorted((grafana / "dashboards").glob("*.json"))
        },
        "aiops-realm": {
            "aiops-realm.json": (PLATFORM / "keycloak" / "realm" / "aiops-realm.json", False)
        },
        # the compose broker keeps a plain, anonymous listener on 1883 for its dev fixture; the cluster's has only the mTLS one
        "aiops-postgres-hba": {"pg_hba.conf": (TARGET / "pg_hba.conf", False)},
        "aiops-mosquitto": {
            "mosquitto.conf": (TARGET / "mosquitto.conf", False),
            "acl.conf": (PLATFORM / "mosquitto" / "acl.conf", False),
        },
    }


def policy_archive() -> bytes:
    """The OPA bundle as ONE deterministic .tar.gz. A ConfigMap volume is a tree of hidden `..data` links that OPA would load as data under
    paths the bundle's manifest does not permit (the pod crash-looped on exactly that), so the policy travels as an archive OPA reads whole."""
    buffer = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w") as tar,
    ):
        for path, (source, _) in sorted(_files()["aiops-policy"].items()):
            data = source.read_bytes().replace(b"\r\n", b"\n")
            info = tarfile.TarInfo(path)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def bundle(name: str) -> dict[str, tuple[str, bytes]]:
    """ConfigMap key -> (path inside the pod, content)."""
    if name == "aiops-policy":
        return {"bundle.tar.gz": ("bundle.tar.gz", policy_archive())}
    out = {}
    for path, (source, rewrite) in _files()[name].items():
        text = source.read_text(encoding="utf-8")
        out[_key(path)] = (path, (rewrite_hosts(text) if rewrite else text).encode("utf-8"))
    return out


def names() -> list[str]:
    return list(_files())


def items(name: str) -> list[dict]:
    """The `items` of a ConfigMap volume, restoring each file's real path."""
    return [{"key": key, "path": path} for key, (path, _) in bundle(name).items()]
