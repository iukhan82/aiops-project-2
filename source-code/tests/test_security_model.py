"""P09.01: the threat model's own rules, without running its evidence (the verifier does that against the real evidence files)."""

import json
from pathlib import Path

import pytest

from security import verify_threat_model as vtm

CONTROLS = {c["id"]: c for c in json.loads(vtm.CONTROLS.read_text(encoding="utf-8"))["controls"]}
MODEL = json.loads(vtm.MODEL.read_text(encoding="utf-8"))


def threat(likelihood: str, safety: str, security: str, treatments: list[str]) -> dict:
    return {
        "likelihood": likelihood,
        "safety_impact": safety,
        "security_impact": security,
        "treatments": treatments,
    }


def controls(*statuses: str) -> dict[str, dict]:
    return {f"C{i}": {"status": s} for i, s in enumerate(statuses)}


def test_a_planned_or_partial_control_earns_no_credit() -> None:
    c = controls("planned", "partial")
    r = vtm.risk(threat("high", "high", "low", ["C0", "C1"]), c)
    assert r["inherent"] == r["residual"] == 9
    assert r["status"] == "open"


def test_each_implemented_control_lowers_likelihood_one_step_and_no_more_than_two() -> None:
    c = controls("implemented", "implemented", "implemented")
    assert vtm.risk(threat("high", "medium", "medium", ["C0"]), c)["residual"] == 4
    assert vtm.risk(threat("high", "medium", "medium", ["C0", "C1"]), c)["residual"] == 2
    assert vtm.risk(threat("high", "medium", "medium", ["C0", "C1", "C2"]), c)["residual"] == 2
    assert vtm.risk(threat("low", "medium", "medium", ["C0", "C1"]), c)["residual"] == 2


def test_safety_and_security_residuals_are_kept_apart() -> None:
    c = controls("implemented")
    r = vtm.risk(threat("medium", "high", "low", ["C0"]), c)
    assert (r["residual_safety"], r["residual_security"]) == (3, 1)


def test_status_is_derived_from_the_controls() -> None:
    assert (
        vtm.risk(threat("low", "low", "low", ["C0", "C1"]), controls("implemented", "planned"))[
            "status"
        ]
        == "partially treated"
    )
    assert (
        vtm.risk(threat("low", "low", "low", ["C0"]), controls("implemented"))["status"]
        == "treated"
    )


def test_levels() -> None:
    assert [vtm.level(n) for n in (1, 2, 3, 4, 6, 9)] == [
        "low",
        "low",
        "medium",
        "medium",
        "high",
        "high",
    ]


def test_the_shipped_files_reference_only_things_that_exist() -> None:
    assert all(set(t["treatments"]) <= set(CONTROLS) for t in MODEL["threats"])
    assert {t["boundary"] for t in MODEL["threats"]} <= {b["id"] for b in MODEL["boundaries"]}
    assert vtm.zones_in_diagram() <= {b["zone"] for b in MODEL["boundaries"]}


DOCS_ABSENT = "markdown project documents are not stored in the repository"


@pytest.mark.skipif(not vtm.REGISTER.is_file(), reason=DOCS_ABSENT)
@pytest.mark.parametrize(
    "control",
    [c for c in CONTROLS.values() if c["status"] in ("planned", "partial")],
    ids=lambda c: c["id"],
)
def test_every_open_control_names_a_registered_task_and_what_is_missing(control: dict) -> None:
    assert control["task"] in vtm.registered_tasks()
    assert control["gap"] and control["due"]


@pytest.mark.skipif(not (vtm.RISK_DOC.is_file() and vtm.THREAT_DOC.is_file()), reason=DOCS_ABSENT)
def test_the_document_tables_are_current() -> None:
    rows = vtm.rows_of(MODEL, CONTROLS)
    assert vtm.apply_blocks(vtm.RISK_DOC, vtm.generated_risk_tables(rows, CONTROLS), write=False)
    assert vtm.apply_blocks(
        vtm.THREAT_DOC,
        vtm.generated_threat_tables(MODEL, list(CONTROLS.values()), rows),
        write=False,
    )


def test_evidence_state_refuses_a_missing_check(tmp_path: Path) -> None:
    ok, why = vtm.evidence_state(
        {"file": "docs/evidence/p08_04_auth_api.json", "checks": ["there_is_no_such_check"]}
    )
    assert not ok and "missing" in why
