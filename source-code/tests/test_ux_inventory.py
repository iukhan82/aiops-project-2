"""P08.01: the UX inventory is consistent with the binding role model and with
itself. (Agreement with the endpoints the FastAPI apps really serve needs the
apps imported and is checked by `frontend/tools/inventory.py`, whose evidence is
docs/evidence/p08_01_inventory.json.)"""

import importlib.util
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
_spec = importlib.util.spec_from_file_location("ux_inventory", FRONTEND / "tools" / "inventory.py")
inventory = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inventory)

INV = inventory.load()


def test_the_inventory_has_no_structural_problems() -> None:
    assert inventory.problems(INV) == []


def test_only_the_auditor_holds_audit_and_only_the_demo_operator_holds_demo() -> None:
    assert INV["capabilities"]["audit.view"]["roles"] == ["auditor"]
    assert INV["capabilities"]["demo.control"]["roles"] == ["demo_operator"]


def test_no_role_holds_an_execute_capability_because_humans_never_execute() -> None:
    assert not [c for c in INV["capabilities"] if "execute" in c]
    assert not [a for a in INV["apis"] if "execute" in a["path"]]


def test_the_field_responder_holds_no_write_capability() -> None:
    writes = ("manage", "dispatch", "request", "review", "write", "control")
    held = [c for c, spec in INV["capabilities"].items() if "field_responder" in spec["roles"]]
    assert not [c for c in held if c.endswith(writes)]


def test_the_demo_operator_cannot_reach_any_operational_write_or_command_capability() -> None:
    held = {c for c, spec in INV["capabilities"].items() if "demo_operator" in spec["roles"]}
    assert held == {"map.view", "analytics.view", "demo.control"}


def test_every_write_api_names_a_capability_and_no_role_can_call_it_without_one() -> None:
    for api in INV["apis"]:
        if api["method"] in ("POST", "PUT", "PATCH", "DELETE"):
            assert api["capability"] is not None, api["id"]


def test_the_supervisor_home_is_the_approval_queue_and_the_auditor_home_the_audit_trail() -> None:
    assert INV["roles"]["supervisor"]["home"] == "actions-commands"
    assert INV["roles"]["auditor"]["home"] == "audit"
