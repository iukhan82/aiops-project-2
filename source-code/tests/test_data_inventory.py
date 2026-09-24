"""P09.06: the parts of the data-inventory review that do not need a live database - the JSON's own internal shape
and its agreement with the contracts and retention windows. The live-database cross-check is
`security/verify_data_inventory.py` (docs/evidence/p09_06_data_inventory.json)."""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from database.retention import RETENTION_DAYS  # noqa: E402
from security.verify_data_inventory import (  # noqa: E402
    contract_fields,
    device_type_enum,
    load,
    privacy_classification_enum,
)

DATA = load()
CATEGORIES = {c["id"]: c for c in DATA["categories"]}
REQUIRED_FIELDS = {
    "id",
    "title",
    "purpose",
    "device_types",
    "privacy_classification",
    "retention_class",
    "personal_data",
    "minimization",
    "access_capabilities",
    "data_subject",
    "rights_process",
    "residual_risk",
    "owner",
}


def test_category_ids_are_unique() -> None:
    assert len(CATEGORIES) == len(DATA["categories"])


def test_every_category_has_every_required_field() -> None:
    for cat in DATA["categories"]:
        assert REQUIRED_FIELDS <= set(cat), cat["id"]


def test_every_category_states_purpose_minimization_rights_and_owner_non_empty() -> None:
    for cat in DATA["categories"]:
        for field in ("purpose", "minimization", "data_subject", "rights_process", "owner"):
            assert cat[field].strip(), (cat["id"], field)


def test_residual_risk_is_one_of_the_three_levels() -> None:
    for cat in DATA["categories"]:
        assert cat["residual_risk"] in ("low", "medium", "high"), cat["id"]


def test_personal_data_flag_agrees_with_the_data_subject_being_a_person() -> None:
    """A category marked personal_data=True must name a human data subject, not 'None'/'Not applicable'."""
    for cat in DATA["categories"]:
        if cat["personal_data"]:
            assert cat["data_subject"].strip().lower() not in ("none.", "not applicable.", ""), cat[
                "id"
            ]


def test_a_category_with_no_personal_data_names_no_rights_process_that_promises_one() -> None:
    for cat in DATA["categories"]:
        if not cat["personal_data"]:
            assert (
                "not applicable" in cat["rights_process"].lower()
                or "not personal data" in cat["rights_process"].lower()
                or "no identifier" in cat["rights_process"].lower()
            ), cat["id"]


def test_retention_classes_are_real() -> None:
    for cat in DATA["categories"]:
        assert cat["retention_class"] in RETENTION_DAYS, cat["id"]


def test_the_audit_class_categories_are_exactly_the_personal_data_ones_that_must_never_expire() -> (
    None
):
    audit_categories = {c["id"] for c in DATA["categories"] if c["retention_class"] == "audit"}
    assert audit_categories == {
        "operator-and-service-identity",
        "policy-and-command-decision-records",
        "demo-identities-and-scenario-runs",
    }


def test_privacy_classifications_are_in_the_device_contract_enum() -> None:
    enum = privacy_classification_enum()
    for cat in DATA["categories"]:
        for value in cat["privacy_classification"]:
            assert value in enum, (cat["id"], value)


def test_device_types_are_in_the_device_contract_enum() -> None:
    enum = device_type_enum()
    for cat in DATA["categories"]:
        for device_type in cat.get("device_types", []):
            assert device_type in enum, (cat["id"], device_type)


def test_every_device_type_appears_in_at_most_one_category() -> None:
    seen: dict[str, str] = {}
    for cat in DATA["categories"]:
        for device_type in cat.get("device_types", []):
            assert device_type not in seen, (device_type, seen[device_type], cat["id"])
            seen[device_type] = cat["id"]


def test_emergency_contracts_carry_no_caller_or_crew_identifier() -> None:
    forbidden = {
        "caller_name",
        "caller_phone",
        "phone",
        "name",
        "crew",
        "crew_member",
        "responder_name",
    }
    assert not (contract_fields("emergency-call") & forbidden)
    assert not (contract_fields("emergency-unit-assignment") & forbidden)


def test_dpia_summary_does_not_claim_certification_or_a_legal_basis() -> None:
    text = DATA["dpia_summary"]["not_claimed"].lower()
    assert "no production" in text or "no jurisdiction" in text or "no certification" in text
    assert DATA["dpia_summary"]["processing_is_high_risk"] is False


def test_retention_class_review_covers_exactly_the_four_real_classes() -> None:
    assert set(DATA["retention_class_review"]) == set(RETENTION_DAYS)
