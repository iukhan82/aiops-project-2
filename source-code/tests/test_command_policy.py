"""P07.05 (P02.08 roles): the pure policy decision function, on hand-built
contexts. Real target validation, live-evidence freshness, safety-class
escalation from a real critical incident, the database-backed state machine
and idempotency are proven by backend/control/verify_commands.py
(docs/evidence/p07_05_commands.json)."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.control.policy import PolicyContext, evaluate
from backend.roles import (
    APPROVE_ROLES,
    FOUR_EYES_CLASSES,
    REQUEST_ROLES,
    SAFETY_CLASS_OF_ACTION,
    safety_class,
)

NOW = datetime(2026, 9, 18, 9, 0, 0, tzinfo=timezone.utc)


def ctx(**overrides) -> PolicyContext:
    base = dict(
        action_type="diversion",
        target_adapter="diversion_adapter",
        target_entity_id="seg-1",
        requested_by="operator:alice",
        expires_at=NOW + timedelta(minutes=5),
        approver="supervisor:bob",
        approver_role="supervisor",
        now=NOW,
        target_exists=True,
        target_evidence_fresh=True,
        recommendation_id=None,
        recommendation_action_type=None,
        recommendation_status=None,
        safety_class="SC-1",
        requester_role="operator",
    )
    base.update(overrides)
    return PolicyContext(**base)


def test_a_fully_valid_command_is_approved() -> None:
    assert evaluate(ctx()).decision == "approved"


def test_an_expired_command_is_reported_expired_not_denied() -> None:
    result = evaluate(ctx(expires_at=NOW - timedelta(seconds=1)))
    assert result.decision == "expired" and result.error_code == "expired"


def test_the_requester_cannot_approve_their_own_command_four_eyes() -> None:
    result = evaluate(ctx(approver="operator:alice"))
    assert (
        result.decision == "denied"
        and result.error_code == "policy_denied"
        and "four-eyes" in result.message
    )


def test_sc_0_advisory_needs_no_second_person() -> None:
    result = evaluate(
        ctx(
            safety_class="SC-0",
            action_type="variable_message_sign",
            approver="operator:alice",
            approver_role="operator",
        )
    )
    assert result.decision == "approved"


def test_an_operator_may_not_approve_an_sc_1_action() -> None:
    result = evaluate(ctx(approver_role="operator"))
    assert result.decision == "denied" and result.error_code == "policy_denied"


def test_sc_2_is_approved_by_a_supervisor_or_an_incident_commander_and_by_nobody_else() -> None:
    for role, expected in (
        ("supervisor", "approved"),
        ("incident_commander", "approved"),
        ("operator", "denied"),
        ("dispatcher", "denied"),
        ("field_responder", "denied"),
        ("auditor", "denied"),
        ("demo_operator", "denied"),
    ):
        result = evaluate(
            ctx(
                safety_class="SC-2",
                action_type="emergency_preemption",
                requester_role="dispatcher",
                approver_role=role,
            )
        )
        assert result.decision == expected, role


def test_a_requester_whose_role_could_not_request_the_class_is_denied_at_approval() -> None:
    result = evaluate(ctx(requester_role="dispatcher"))  # a dispatcher holds REQUEST only for SC-2
    assert result.decision == "denied" and "may not request" in result.message


def test_a_legacy_command_with_no_recorded_requester_role_is_not_blocked_by_the_requester_check() -> (
    None
):
    assert evaluate(ctx(requester_role=None)).decision == "approved"


def test_the_role_tables_match_the_binding_authority_document() -> None:
    assert SAFETY_CLASS_OF_ACTION["variable_message_sign"] == "SC-0"
    assert {
        SAFETY_CLASS_OF_ACTION[a] for a in ("signal_plan_change", "diversion", "transit_priority")
    } == {"SC-1"}
    assert SAFETY_CLASS_OF_ACTION["emergency_preemption"] == "SC-2"
    assert REQUEST_ROLES == {
        "SC-0": {"operator"},
        "SC-1": {"operator"},
        "SC-2": {"operator", "dispatcher"},
    }
    assert APPROVE_ROLES["SC-1"] == {"supervisor"} and APPROVE_ROLES["SC-2"] == {
        "supervisor",
        "incident_commander",
    }
    assert FOUR_EYES_CLASSES == {"SC-1", "SC-2"}


def test_an_active_critical_incident_raises_any_action_to_sc_2() -> None:
    assert safety_class("diversion", critical_incident_on_target=True) == "SC-2"
    assert safety_class("variable_message_sign", critical_incident_on_target=True) == "SC-2"
    assert safety_class("diversion") == "SC-1"


@pytest.mark.parametrize("cls", ["SC-0", "SC-1", "SC-2"])
def test_every_safety_class_has_an_approving_and_a_requesting_role(cls: str) -> None:
    assert APPROVE_ROLES[cls] and REQUEST_ROLES[cls]


def test_an_unknown_adapter_is_an_invalid_target() -> None:
    result = evaluate(ctx(target_adapter="made_up_adapter"))
    assert result.decision == "denied" and result.error_code == "invalid_target"


def test_a_target_that_does_not_exist_is_an_invalid_target() -> None:
    result = evaluate(ctx(target_exists=False))
    assert result.decision == "denied" and result.error_code == "invalid_target"


def test_a_missing_linked_recommendation_is_an_invalid_target() -> None:
    result = evaluate(ctx(recommendation_id="r1", recommendation_status=None))
    assert result.decision == "denied" and result.error_code == "invalid_target"


def test_a_recommendation_for_a_different_action_type_is_denied() -> None:
    result = evaluate(
        ctx(
            recommendation_id="r1",
            recommendation_action_type="signal_plan_change",
            recommendation_status="proposed",
        )
    )
    assert result.decision == "denied" and result.error_code == "policy_denied"


def test_a_superseded_or_expired_recommendation_is_no_longer_actionable() -> None:
    for status in ("superseded", "expired"):
        result = evaluate(
            ctx(
                recommendation_id="r1",
                recommendation_action_type="diversion",
                recommendation_status=status,
            )
        )
        assert result.decision == "denied" and result.error_code == "policy_denied"


def test_a_proposed_or_requested_recommendation_is_actionable() -> None:
    for status in ("proposed", "requested"):
        result = evaluate(
            ctx(
                recommendation_id="r1",
                recommendation_action_type="diversion",
                recommendation_status=status,
            )
        )
        assert result.decision == "approved"


def test_stale_evidence_is_denied_with_the_stale_evidence_error_code_and_marked_retryable() -> None:
    result = evaluate(ctx(target_evidence_fresh=False))
    assert result.decision == "denied" and result.error_code == "stale_evidence"
    assert result.as_error_record()["retryable"] is True


def test_expiry_is_checked_before_anything_else_even_a_role_violation() -> None:
    result = evaluate(ctx(expires_at=NOW - timedelta(seconds=1), approver_role="operator"))
    assert result.decision == "expired" and result.error_code == "expired"


def test_approved_has_no_error_record() -> None:
    assert evaluate(ctx()).as_error_record() is None
