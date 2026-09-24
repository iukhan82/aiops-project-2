"""P05.08: repository state-machine unit tests (pure logic, no database
needed). Real-database behavior (row-lock concurrency, idempotent create,
audit-reference enforcement) needs the real P05.01/P05.04 Postgres and is
proven by source-code/backend/repositories/verify_repositories.py, not
here - see docs/evidence/p05_08_repositories.json.
"""

from backend.repositories import commands, incidents


def test_incident_state_machine_covers_every_contract_status() -> None:
    contract_statuses = {
        "open",
        "acknowledged",
        "investigating",
        "escalated",
        "resolved",
        "reopened",
    }
    assert set(incidents.ALLOWED_TRANSITIONS) == contract_statuses


def test_incident_resolved_can_only_reopen() -> None:
    assert incidents.ALLOWED_TRANSITIONS["resolved"] == {"reopened"}


def test_incident_open_cannot_jump_to_reopened() -> None:
    assert "reopened" not in incidents.ALLOWED_TRANSITIONS["open"]


def test_command_state_machine_covers_every_contract_status() -> None:
    contract_statuses = {
        "requested",
        "approved",
        "denied",
        "executing",
        "executed",
        "failed",
        "rolled_back",
        "expired",
    }
    assert set(commands.ALLOWED_TRANSITIONS) == contract_statuses


def test_command_terminal_states_have_no_outgoing_transitions() -> None:
    for terminal in ("denied", "failed", "expired", "rolled_back"):
        assert commands.ALLOWED_TRANSITIONS[terminal] == set()


def test_command_requested_cannot_skip_straight_to_executed() -> None:
    assert "executed" not in commands.ALLOWED_TRANSITIONS["requested"]


def test_error_required_statuses_match_contract_note() -> None:
    # contracts/command/v1: error is "present only when status is 'failed'
    # or policy_decision is 'denied'/'policy_unavailable'" - denied is the
    # status-level counterpart this repository enforces.
    assert commands.ERROR_REQUIRED_STATUSES == {"denied", "failed"}
