"""P09.03: generates the data document the Rego policies read (`aiops/model/data.json`).

    python source-code/policy/build_data.py            # rewrite the file
    python source-code/policy/build_data.py --check    # fail if the file on disk is not what the sources produce

The policies carry no role, route or adapter of their own. Everything they decide over comes from the two places the platform already
keeps it, so a change there reaches the policy engine without a second edit and a stale copy is caught:

* `frontend/src/config/inventory.json` (P08.01) - which capability each role holds and which capability each endpoint needs;
* `backend/roles.py` and `backend/control/policy.py` (P02.08, P07.05) - safety classes, who may request and approve, the adapter registry.

`version` is a short hash of the policy text and the data, so a decision names the exact policy that made it and a policy change is a
visible change of version. Tests (`*_test.rego`) are not part of the hash: they are not part of what is served.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.api.authz import inventory  # noqa: E402
from backend.control.policy import ADAPTER_TARGET_KIND, KNOWN_ADAPTERS  # noqa: E402
from backend.roles import (  # noqa: E402
    APPROVE_ROLES,
    EXECUTOR,
    FOUR_EYES_CLASSES,
    HUMAN_ROLES,
    OVERRIDE_ROLES,
    REQUEST_ROLES,
    SAFETY_CLASS_OF_ACTION,
    SERVICE_IDENTITIES,
)

POLICY_DIR = Path(__file__).resolve().parent
DATA_PATH = POLICY_DIR / "aiops" / "model" / "data.json"
ACTIONABLE_RECOMMENDATION_STATUSES = ["proposed", "requested"]


def rego_files() -> list[Path]:
    return sorted(p for p in POLICY_DIR.rglob("*.rego") if not p.name.endswith("_test.rego"))


def build() -> dict:
    inv = inventory()
    assert set(ADAPTER_TARGET_KIND) == set(KNOWN_ADAPTERS), (
        "the adapter registry and its target kinds disagree"
    )
    data = {
        "roles": sorted(set(HUMAN_ROLES) | set(SERVICE_IDENTITIES)),
        "capabilities": {
            name: {"roles": sorted(spec["roles"])}
            for name, spec in sorted(inv["capabilities"].items())
        },
        "endpoints": {
            f"{a.get('service', 'api')} {a['method']} {a['path']}": {
                "capability": a["capability"],
                "public": bool(a.get("public")),
            }
            for a in sorted(
                inv["apis"], key=lambda a: (a.get("service", "api"), a["path"], a["method"])
            )
        },
        "safety_class_of_action": dict(sorted(SAFETY_CLASS_OF_ACTION.items())),
        "request_roles": {c: sorted(r) for c, r in sorted(REQUEST_ROLES.items())},
        "approve_roles": {c: sorted(r) for c, r in sorted(APPROVE_ROLES.items())},
        "override_roles": {c: sorted(r) for c, r in sorted(OVERRIDE_ROLES.items())},
        "four_eyes_classes": sorted(FOUR_EYES_CLASSES),
        "adapter_target_kind": dict(sorted(ADAPTER_TARGET_KIND.items())),
        "executor": EXECUTOR,
        "actionable_recommendation_statuses": ACTIONABLE_RECOMMENDATION_STATUSES,
    }
    digest = hashlib.sha256()
    for path in rego_files():
        digest.update(path.relative_to(POLICY_DIR).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    digest.update(json.dumps(data, sort_keys=True).encode())
    data["version"] = digest.hexdigest()[:16]
    return data


def render(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    wanted = render(build())
    current = DATA_PATH.read_text(encoding="utf-8") if DATA_PATH.is_file() else ""
    if args.check:
        if current.replace("\r\n", "\n") != wanted:
            print(f"{DATA_PATH} is out of date; run policy/build_data.py", file=sys.stderr)
            return 1
        print(f"policy data is current (version {json.loads(wanted)['version']})")
        return 0
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(wanted, encoding="utf-8", newline="\n")
    print(f"wrote {DATA_PATH} (version {json.loads(wanted)['version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
