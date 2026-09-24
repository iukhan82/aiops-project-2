package aiops.command_test

import data.aiops.command
import rego.v1

fixture := {
	"version": "test",
	"roles": ["operator", "supervisor", "dispatcher", "incident_commander"],
	"safety_class_of_action": {
		"variable_message_sign": "SC-0",
		"diversion": "SC-1",
		"emergency_preemption": "SC-2",
	},
	"request_roles": {"SC-0": ["operator"], "SC-1": ["operator"], "SC-2": ["dispatcher", "operator"]},
	"approve_roles": {"SC-0": ["operator", "supervisor"], "SC-1": ["supervisor"], "SC-2": ["incident_commander", "supervisor"]},
	"override_roles": {"SC-0": [], "SC-1": [], "SC-2": ["incident_commander"]},
	"four_eyes_classes": ["SC-1", "SC-2"],
	"adapter_target_kind": {"diversion_adapter": "segment", "emergency_preemption_adapter": "corridor"},
	"executor": "system:command-executor",
	"actionable_recommendation_statuses": ["proposed", "requested"],
}

approval := {
	"phase": "approval",
	"action_type": "diversion",
	"critical_incident_on_target": false,
	"now": "2026-09-18T09:00:00+00:00",
	"expires_at": "2026-09-18T09:05:00+00:00",
	"target": {"adapter": "diversion_adapter", "entity_id": "seg-1", "exists": true, "evidence_fresh": true},
	"requester": {"id": "operator:alice", "role": "operator"},
	"approver": {"id": "supervisor:bob", "role": "supervisor"},
	"recommendation": null,
}

execution := {
	"phase": "execution",
	"actor": "system:command-executor",
	"command_status": "approved",
	"action_type": "diversion",
	"critical_incident_on_target": false,
	"now": "2026-09-18T09:01:00+00:00",
	"expires_at": "2026-09-18T09:05:00+00:00",
	"target": {"adapter": "diversion_adapter", "entity_id": "seg-1", "exists": true, "evidence_fresh": true},
	"requester": {"id": "operator:alice", "role": "operator"},
	"approver": {"id": "supervisor:bob", "role": "supervisor"},
}

decide(facts) := result if {
	result := command.decision with data.aiops.model as fixture with input as facts
}

authority(facts) := result if {
	result := command.authority with data.aiops.model as fixture with input as facts
}

test_valid_approval if decide(approval).decision == "approved"

test_valid_execution if decide(execution).decision == "approved"

test_expired_is_reported_before_a_role_violation if {
	d := decide(object.union(approval, {"now": "2026-09-18T09:06:00+00:00", "approver": {"id": "x", "role": "operator"}}))
	d.decision == "expired"
	d.error_code == "expired"
}

test_four_eyes if {
	d := decide(object.union(approval, {"approver": {"id": "operator:alice", "role": "supervisor"}}))
	d.decision == "denied"
	contains(d.message, "four-eyes")
}

test_sc0_needs_no_second_person if {
	d := decide(object.union(approval, {"action_type": "variable_message_sign", "approver": {"id": "operator:alice", "role": "operator"}}))
	d.decision == "approved"
	d.safety_class == "SC-0"
}

test_role_cannot_approve_sc1 if {
	d := decide(object.union(approval, {"approver": {"id": "operator:carol", "role": "operator"}}))
	d.decision == "denied"
	d.message == "role 'operator' may not approve a SC-1 action ('diversion')"
}

test_critical_incident_raises_class_to_sc2 if {
	ic := decide(object.union(approval, {"critical_incident_on_target": true, "approver": {"id": "x", "role": "incident_commander"}}))
	ic.safety_class == "SC-2"
	ic.decision == "approved"
	sup := decide(object.union(approval, {"critical_incident_on_target": true, "approver": {"id": "x", "role": "operator"}}))
	sup.decision == "denied"
	sup.message == "role 'operator' may not approve a SC-2 action ('diversion')"
}

test_requester_role_must_be_able_to_request if {
	d := decide(object.union(approval, {"requester": {"id": "operator:alice", "role": "dispatcher"}}))
	d.decision == "denied"
	contains(d.message, "may not request")
}

test_legacy_command_without_requester_role_is_not_blocked_by_it if {
	d := decide(object.union(approval, {"requester": {"id": "operator:alice", "role": null}}))
	d.decision == "approved"
}

test_unknown_adapter if {
	d := decide(object.union(approval, {"target": {"adapter": "made_up", "entity_id": "seg-1", "exists": true, "evidence_fresh": true}}))
	d.error_code == "invalid_target"
}

test_target_that_does_not_exist if {
	d := decide(object.union(approval, {"target": {"adapter": "diversion_adapter", "entity_id": "seg-1", "exists": false, "evidence_fresh": true}}))
	d.error_code == "invalid_target"
}

test_stale_evidence if {
	d := decide(object.union(approval, {"target": {"adapter": "diversion_adapter", "entity_id": "seg-1", "exists": true, "evidence_fresh": false}}))
	d.error_code == "stale_evidence"
}

test_recommendation_must_exist if {
	d := decide(object.union(approval, {"recommendation": {"id": "r1", "action_type": null, "status": null}}))
	d.error_code == "invalid_target"
}

test_recommendation_action_must_match if {
	d := decide(object.union(approval, {"recommendation": {"id": "r1", "action_type": "emergency_preemption", "status": "proposed"}}))
	d.error_code == "policy_denied"
}

test_recommendation_must_be_actionable if {
	d := decide(object.union(approval, {"recommendation": {"id": "r1", "action_type": "diversion", "status": "superseded"}}))
	d.error_code == "policy_denied"
}

test_actionable_recommendation_is_accepted if {
	d := decide(object.union(approval, {"recommendation": {"id": "r1", "action_type": "diversion", "status": "requested"}}))
	d.decision == "approved"
}

test_only_the_executor_may_execute if {
	d := decide(object.union(execution, {"actor": "supervisor:bob"}))
	d.decision == "denied"
	contains(d.message, "only 'system:command-executor' holds EXECUTE authority")
}

test_only_an_approved_command_is_executed if {
	d := decide(object.union(execution, {"command_status": "requested"}))
	d.decision == "denied"
}

test_execution_needs_a_recorded_approval if {
	d := decide(object.union(execution, {"approver": {"id": null, "role": null}}))
	d.decision == "denied"
	d.message == "no approval is recorded for this command"
}

test_execution_rechecks_expiry if {
	d := decide(object.union(execution, {"now": "2026-09-18T09:06:00+00:00"}))
	d.decision == "expired"
}

test_execution_rechecks_that_the_recorded_approval_was_legitimate if {
	d := decide(object.union(execution, {"approver": {"id": "operator:alice", "role": "supervisor"}}))
	d.decision == "denied"
	contains(d.message, "four-eyes")
}

test_execution_rechecks_evidence_freshness if {
	d := decide(object.union(execution, {"target": {"adapter": "diversion_adapter", "entity_id": "seg-1", "exists": true, "evidence_fresh": false}}))
	d.error_code == "stale_evidence"
}

test_execution_ignores_the_recommendation_state if {
	d := decide(object.union(execution, {"recommendation": {"id": "r1", "action_type": "diversion", "status": "superseded"}}))
	d.decision == "approved"
}

test_missing_field_is_denied_not_approved if {
	facts := {k: v | some k, v in approval; k != "target"}
	d := decide(facts)
	d.decision == "denied"
	contains(d.message, "malformed")
}

test_unparseable_time_is_denied_not_approved if {
	d := decide(object.union(approval, {"expires_at": "tomorrow"}))
	d.decision == "denied"
	contains(d.message, "malformed")
}

test_unknown_phase_is_denied if {
	d := decide(object.union(approval, {"phase": "override"}))
	d.decision == "denied"
}

test_empty_input_is_denied if {
	d := decide({})
	d.decision == "denied"
}

test_wrongly_typed_flag_is_denied if {
	d := decide(object.union(approval, {"target": {"adapter": "diversion_adapter", "entity_id": "seg-1", "exists": "yes", "evidence_fresh": true}}))
	d.decision == "denied"
}

test_authority_request_names_the_role_used if {
	a := authority({"kind": "request", "action_type": "diversion", "critical_incident_on_target": false, "roles": ["auditor", "operator"]})
	a.allow
	a.role == "operator"
	a.safety_class == "SC-1"
}

test_authority_dispatcher_may_request_sc2_only if {
	sc1 := authority({"kind": "request", "action_type": "diversion", "critical_incident_on_target": false, "roles": ["dispatcher"]})
	not sc1.allow
	sc1.reason == "role_cannot_request"
	sc2 := authority({"kind": "request", "action_type": "emergency_preemption", "critical_incident_on_target": false, "roles": ["dispatcher"]})
	sc2.allow
}

test_authority_review_needs_the_approve_role if {
	a := authority({"kind": "review", "action_type": "diversion", "critical_incident_on_target": false, "roles": ["operator"]})
	not a.allow
	a.reason == "role_cannot_review"
}

test_authority_malformed_input_is_denied if {
	a := authority({"kind": "not_a_real_kind", "action_type": "diversion", "critical_incident_on_target": false, "roles": ["operator"]})
	not a.allow
	a.reason == "malformed_authority_input"
}

test_authority_override_request_names_incident_commander_for_sc2 if {
	a := authority({"kind": "override", "action_type": "emergency_preemption", "critical_incident_on_target": false, "roles": ["supervisor", "incident_commander"]})
	a.allow
	a.role == "incident_commander"
	a.safety_class == "SC-2"
}

test_authority_override_is_unavailable_for_sc0_and_sc1 if {
	sc0 := authority({"kind": "override", "action_type": "variable_message_sign", "critical_incident_on_target": false, "roles": ["incident_commander"]})
	not sc0.allow
	sc0.reason == "role_cannot_override"
	sc1 := authority({"kind": "override", "action_type": "diversion", "critical_incident_on_target": false, "roles": ["incident_commander"]})
	not sc1.allow
	sc1.reason == "role_cannot_override"
}

test_authority_empty_roles_is_denied if {
	a := authority({"kind": "request", "action_type": "diversion", "critical_incident_on_target": false, "roles": []})
	not a.allow
}

test_approval_by_a_person_with_no_recorded_role_is_denied_for_the_role if {
	d := decide(object.union(approval, {"approver": {"id": "x", "role": null}}))
	d.decision == "denied"
	d.message == "role None may not approve a SC-1 action ('diversion')"
}
