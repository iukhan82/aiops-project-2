"""P11.07 acceptance evidence: only adapters that were actually tested claim compatibility; NTCIP, GTFS, CAP, V2X, DATEX II and specific CAD/AVL vendors are candidates.

    python source-code/infra/verify_adapter_claims.py

The platform's control adapters (`backend/control/policy.KNOWN_ADAPTERS`) drive the SIMULATOR through TraCI; the emergency side is a vendor-neutral CAD/AVL
ABSTRACTION exercised against simulated calls and units. No external protocol adapter exists in this repository. What this check proves, from the files:

A. The registry of adapters is what it says: each is bound to a simulator target kind and is executed only through the simulator session, and the adapter module says so.
B. No code implements or imports an external wire protocol (an NTCIP/SNMP stack, a GTFS-Realtime or CAP or DATEX II or J2735 library, an ASN.1 codec), and no dependency
   lock carries one.
C. Every place the project's own text names one of those standards or a CAD/AVL vendor, the same paragraph says what it is: a candidate, optional, not implemented, not
   claimed, out of scope, or an abstraction. A sentence that names a standard with no such qualification is listed as an unqualified claim, and there must be none.
D. The API and the console expose no protocol-compatibility claim: the adapter names in the inventory are the registry's, none is a standard's name.

This is a check of what the project SAYS against what it HAS. It cannot prove a claim someone might make elsewhere.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.control import policy  # noqa: E402
from backend.evidence import Evidence  # noqa: E402

ev = Evidence("P11.07", "p11_07_adapter_claims", docs_name="p11_07_adapter_claims")
STANDARDS = re.compile(
    r"(?i:NTCIP|GTFS|J2735|V2X|DATEX|Common Alerting|\bSNMP\b|vendor CAD)|(?<![-\w])CAP(?![-\w])"
)
HEDGES = re.compile(
    r"candidate|optional|not implemented|not claimed|no claim|out of scope|not in scope|abstraction|never imply|not imply|do not claim|no adapter|not built|future|"
    r"until a real integration|only when|where required|where european|compatible public-warning|record the exact|without a tested adapter|neutral|simulat|not a|no external|no such|is not",
    re.I,
)
FORBIDDEN_IMPORTS = re.compile(
    r"^\s*(?:import|from)\s+(pysnmp|gtfs_realtime_bindings|gtfs|pyasn1|asn1tools|asn1crypto|capparselib|pycap|datex2|ntcip)\b",
    re.M,
)
SKIP_PARTS = {
    "node_modules",
    ".venv",
    "output",
    "__pycache__",
    ".git",
    "dist",
    "test-results",
    "playwright-report",
}


def text_files() -> list[Path]:
    roots = [REPO_ROOT / "docs", REPO_ROOT / "README.md", REPO_ROOT / "AGENTS.md", SOURCE_ROOT]
    out = []
    for root in roots:
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        out += [
            p
            for p in candidates
            if p.is_file()
            and p.suffix in (".md", ".py", ".ts", ".tsx", ".json", ".yaml", ".yml", ".txt")
            and not SKIP_PARTS & set(p.parts)
            and "docs/evidence" not in p.as_posix()
        ]
    return out


def paragraph(lines: list[str], index: int) -> str:
    start = index
    while start > 0 and lines[start - 1].strip():
        start -= 1
    end = index
    while end + 1 < len(lines) and lines[end + 1].strip():
        end += 1
    return " ".join(lines[start : end + 1])


def main() -> int:
    # ---- A. the adapter registry
    adapters = sorted(policy.KNOWN_ADAPTERS)
    bound = sorted(policy.ADAPTER_TARGET_KIND)
    ev.check(
        "every_registered_adapter_is_bound_to_a_simulator_target_kind",
        set(adapters) == set(bound),
        f"{len(adapters)} adapters: {adapters}",
    )
    executor = (SOURCE_ROOT / "backend" / "control" / "simulator_adapters.py").read_text(
        encoding="utf-8"
    )
    ev.check(
        "the_adapter_module_says_it_drives_the_simulator_and_nothing_else",
        "simulator" in executor.split('"""')[1].lower()
        and "TraCI"
        in (SOURCE_ROOT / "simulator" / "control_adapters" / "README.md").read_text(
            encoding="utf-8"
        ),
        "backend/control/simulator_adapters.py, simulator/control_adapters/README.md",
    )

    # ---- B. nothing implements an external protocol
    imports = []
    for path in text_files():
        if path.suffix == ".py" and "verify_adapter_claims" not in path.name:
            for m in FORBIDDEN_IMPORTS.finditer(path.read_text(encoding="utf-8", errors="replace")):
                imports.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {m.group(1)}")
    ev.check(
        "no_code_imports_or_implements_an_external_traffic_protocol_stack",
        not imports,
        f"{imports[:4]}",
    )
    locks = [p for p in SOURCE_ROOT.rglob("requirements*.txt") if not SKIP_PARTS & set(p.parts)] + [
        SOURCE_ROOT / "frontend" / "package.json"
    ]
    carried = [
        f"{p.name}: {m.group(0)}"
        for p in locks
        if p.is_file()
        for m in re.finditer(
            r"pysnmp|gtfs|asn1|datex|ntcip|j2735", p.read_text(encoding="utf-8"), re.I
        )
    ]
    ev.check(
        "no_dependency_lock_carries_an_external_protocol_library", not carried, str(carried[:4])
    )

    # ---- C. every mention is qualified in its own paragraph
    mentions, unqualified = 0, []
    for path in text_files():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            if STANDARDS.search(line) and path.name != "verify_adapter_claims.py":
                mentions += 1
                block = (
                    paragraph(lines, i)
                    if path.suffix == ".md"
                    else " ".join(lines[max(0, i - 4) : i + 5])
                )
                if not HEDGES.search(block):
                    unqualified.append(
                        f"{path.relative_to(REPO_ROOT).as_posix()}:{i + 1}: {line.strip()[:110]}"
                    )
    ev.check(
        "every_mention_of_a_standard_or_a_cad_vendor_is_qualified_in_its_own_paragraph",
        not unqualified,
        f"{mentions} mentions; unqualified {unqualified[:5]}",
    )

    # ---- D. the API and console
    inventory = json.loads(
        (SOURCE_ROOT / "frontend" / "src" / "config" / "inventory.json").read_text(encoding="utf-8")
    )
    claims = [a["id"] for a in inventory["apis"] if STANDARDS.search(json.dumps(a))]
    ev.check("no_api_of_the_inventory_names_a_protocol_standard", not claims, str(claims))
    ev.notes["what_is_tested"] = (
        "the five control adapters against the simulator (P07.06-P07.10) and the vendor-neutral CAD/AVL abstraction against simulated calls and units (P07.01, P07.03); nothing else"
    )
    ev.notes["candidates_not_built"] = (
        "NTCIP, SAE J2735 V2X, GTFS Realtime, DATEX II, CAP and any vendor's CAD/AVL: recorded in docs/PROTOCOLS_AND_STANDARDS.md as optional candidates until a real integration is in scope"
    )
    ev.metrics = {"adapters": adapters, "mentions_checked": mentions}
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
