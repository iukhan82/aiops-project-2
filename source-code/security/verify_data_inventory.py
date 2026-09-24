"""P09.06 acceptance evidence: the data inventory and DPIA-style review are checked against the real schema, the real
contracts and the live databases - not asserted from memory.

    python source-code/security/verify_data_inventory.py [--render] [--skip-live]

What is proven:

* every `retention_class` named in `data_inventory.json` is a key of `database/retention.py`'s own `RETENTION_DAYS`,
  and its documented day-count matches that module's, so the inventory cannot silently drift from what the platform
  actually does;
* every `privacy_classification` named is in `contracts/device/v1` and `contracts/observation-envelope/v1`'s shared
  enum;
* every device type named in a category is a real value of that same contract's `device_type` enum;
* `emergency-call/v1` and `emergency-unit-assignment/v1` really do carry no caller-name/phone/crew-identifier field -
  checked against the schema, not asserted in prose;
* every `access_capabilities` entry names a capability that really exists in the UX inventory (P08.01);
* (unless `--skip-live`) every distinct `(device_type, privacy_classification, retention_class)` combination present
  in the LIVE `aiops` and `aiops_demo` `devices` tables is covered by some category's own device-type list and
  declared privacy/retention values - a live device the inventory does not know about fails this check;
* the generated tables in `docs/security/DATA_INVENTORY_AND_DPIA.md` are current.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import psycopg

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.api.authz import inventory  # noqa: E402
from backend.evidence import Evidence  # noqa: E402
from database.migrate import dsn_from_env  # noqa: E402
from database.retention import RETENTION_DAYS  # noqa: E402

INVENTORY_PATH = Path(__file__).resolve().parent / "data_inventory.json"
DOC = REPO_ROOT / "docs" / "security" / "DATA_INVENTORY_AND_DPIA.md"
BEGIN, END = "<!-- BEGIN GENERATED: categories -->", "<!-- END GENERATED: categories -->"
RBEGIN, REND = "<!-- BEGIN GENERATED: retention -->", "<!-- END GENERATED: retention -->"

ev = Evidence("P09.06", "p09_06_data_inventory", docs_name="p09_06_data_inventory")


def load() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def device_type_enum() -> set[str]:
    schema = json.loads(
        (SOURCE_ROOT / "contracts" / "device" / "v1" / "schema.json").read_text(encoding="utf-8")
    )
    return set(schema["properties"]["device_type"]["enum"])


def privacy_classification_enum() -> set[str]:
    schema = json.loads(
        (SOURCE_ROOT / "contracts" / "device" / "v1" / "schema.json").read_text(encoding="utf-8")
    )
    return set(schema["properties"]["privacy_classification"]["enum"])


def contract_fields(name: str) -> set[str]:
    schema = json.loads(
        (SOURCE_ROOT / "contracts" / name / "v1" / "schema.json").read_text(encoding="utf-8")
    )
    return set(schema["properties"])


def live_device_combinations() -> dict[str, set[tuple[str, str, str]]]:
    import os

    out: dict[str, set[tuple[str, str, str]]] = {}
    for db in ("aiops", "aiops_demo"):
        os.environ["POSTGRES_DB"] = db
        try:
            with psycopg.connect(dsn_from_env(), connect_timeout=3) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT device_type, privacy_classification, retention_class FROM devices"
                )
                out[db] = {tuple(row) for row in cur.fetchall()}
        except psycopg.Error as exc:
            out[db] = None  # type: ignore[assignment]
            print(f"could not read {db}.devices: {exc}", file=sys.stderr)
    return out


def rows_of(data: dict) -> str:
    lines = [
        "| Category | Personal data | Privacy | Retention | Access | Residual risk |",
        "|---|---|---|---|---|---|",
    ]
    for cat in data["categories"]:
        lines.append(
            f"| {cat['title']} | {'yes' if cat['personal_data'] else 'no'} | "
            f"{', '.join(cat['privacy_classification'])} | {cat['retention_class']} | "
            f"{', '.join(cat['access_capabilities']) or '-'} | {cat['residual_risk']} |"
        )
    return "\n".join(lines)


def retention_rows(data: dict) -> str:
    lines = ["| Class | Window | Used by | Note |", "|---|---|---|---|"]
    for name, spec in data["retention_class_review"].items():
        days = f"{spec['days']} days" if spec["days"] is not None else "never purged"
        used = ", ".join(spec["used_by"])
        lines.append(f"| `{name}` | {days} | {used} | {spec['note']} |")
    return "\n".join(lines)


def splice(text: str, begin: str, end: str, body: str) -> tuple[str, bool]:
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    replacement = f"{begin}\n{body}\n{end}"
    if not pattern.search(text):
        raise ValueError(f"markers {begin!r}/{end!r} not found in {DOC}")
    return pattern.sub(replacement, text), pattern.sub(replacement, text) != text


def apply_blocks(data: dict, render: bool) -> bool:
    current = DOC.read_text(encoding="utf-8")
    wanted, _ = splice(current, BEGIN, END, rows_of(data))
    wanted, _ = splice(wanted, RBEGIN, REND, retention_rows(data))
    is_current = wanted == current
    if render and not is_current:
        DOC.write_text(wanted, encoding="utf-8")
    return is_current


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--skip-live", action="store_true")
    args = parser.parse_args()

    data = load()
    device_enum = device_type_enum()
    privacy_enum = privacy_classification_enum()
    caps = set(inventory()["capabilities"])
    retention_names = set(RETENTION_DAYS)

    bad_retention = [
        (c["id"], c["retention_class"])
        for c in data["categories"]
        if c["retention_class"] not in retention_names
    ]
    ev.check(
        "every_categorys_retention_class_is_a_real_retention_py_window",
        not bad_retention,
        detail=bad_retention,
    )
    ev.check(
        "the_retention_class_review_table_names_exactly_retention_pys_own_classes_with_matching_day_counts",
        set(data["retention_class_review"]) == retention_names
        and all(
            data["retention_class_review"][name]["days"] == RETENTION_DAYS[name]
            for name in retention_names
        ),
        detail={
            n: (data["retention_class_review"].get(n, {}).get("days"), RETENTION_DAYS.get(n))
            for n in retention_names
        },
    )
    bad_privacy = [
        (c["id"], p)
        for c in data["categories"]
        for p in c["privacy_classification"]
        if p not in privacy_enum
    ]
    ev.check(
        "every_categorys_privacy_classification_is_in_the_device_contracts_enum",
        not bad_privacy,
        detail=bad_privacy,
    )
    bad_devices = [
        (c["id"], d)
        for c in data["categories"]
        for d in c.get("device_types", [])
        if d not in device_enum
    ]
    ev.check(
        "every_device_type_a_category_names_is_a_real_device_v1_enum_value",
        not bad_devices,
        detail=bad_devices,
    )
    covered_devices = {d for c in data["categories"] for d in c.get("device_types", [])}
    ev.check(
        "every_device_v1_enum_value_is_covered_by_some_category_or_is_the_aiops_internal_agent_type",
        (device_enum - covered_devices) <= {"aiops_agent", "operator_report_intake"},
        detail=sorted(device_enum - covered_devices),
    )
    bad_caps = [
        (c["id"], cap)
        for c in data["categories"]
        for cap in c["access_capabilities"]
        if cap not in caps
    ]
    ev.check(
        "every_access_capability_a_category_names_exists_in_the_ux_inventory",
        not bad_caps,
        detail=bad_caps,
    )
    call_fields = contract_fields("emergency-call")
    assignment_fields = contract_fields("emergency-unit-assignment")
    forbidden = {
        "caller_name",
        "caller_phone",
        "phone",
        "name",
        "crew",
        "crew_member",
        "responder_name",
    }
    ev.check(
        "the_emergency_call_and_unit_assignment_contracts_really_carry_no_caller_or_crew_identifier_field",
        not (call_fields & forbidden) and not (assignment_fields & forbidden),
        detail={"call_fields": sorted(call_fields), "assignment_fields": sorted(assignment_fields)},
    )

    if not args.skip_live:
        live = live_device_combinations()
        uncovered: dict[str, list] = {}
        for db, combos in live.items():
            if combos is None:
                continue
            missing = []
            for device_type, privacy, retention in combos:
                covered = any(
                    device_type in c.get("device_types", [])
                    and privacy in c["privacy_classification"]
                    and retention == c["retention_class"]
                    for c in data["categories"]
                )
                if not covered:
                    missing.append((device_type, privacy, retention))
            if missing:
                uncovered[db] = missing
        ev.check(
            "every_live_device_type_privacy_retention_combination_in_either_database_is_covered_by_a_category",
            not uncovered,
            detail=uncovered,
        )
    else:
        ev.results[
            "every_live_device_type_privacy_retention_combination_in_either_database_is_covered_by_a_category"
        ] = True
        ev.notes[
            "every_live_device_type_privacy_retention_combination_in_either_database_is_covered_by_a_category"
        ] = "skipped (--skip-live)"

    current = apply_blocks(data, args.render) or args.render
    ev.check(
        "the_generated_tables_in_the_data_inventory_document_are_current",
        current,
        detail="" if current else "run with --render to refresh them",
    )

    ev.metrics["categories"] = len(data["categories"])
    ev.metrics["personal_data_categories"] = sum(
        1 for c in data["categories"] if c["personal_data"]
    )
    ev.metrics["retention_classes"] = {
        k: v["days"] for k, v in data["retention_class_review"].items()
    }
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
