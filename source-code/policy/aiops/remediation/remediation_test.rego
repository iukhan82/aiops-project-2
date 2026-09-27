package aiops.remediation_test

import data.aiops.remediation
import rego.v1

fixture := {
	"version": "test",
	"remediation": {
		"actor": "system:aiops-remediation",
		"approver_roles": ["operator", "supervisor"],
		"max_in_flight": 1,
		"max_attempts_per_incident": 4,
		"held_by_person_statuses": ["acknowledged", "escalated"],
		"actions": {
			"restart_container": {
				"kind": "restart",
				"params": {},
				"max_attempts_per_incident": 2,
				"cooldown_s": 120,
				"window_s": 3600,
				"max_per_window": 3,
				"targets": [
					{"id": "aiops-edge-runtime", "pattern": false, "autonomy": "auto"},
					{"id": "aiops-postgres", "pattern": false, "autonomy": "approval"},
				],
			},
			"set_trace_sampling": {
				"kind": "sampling",
				"params": {"ratio": {"min": 0.01, "max": 0.5}, "ttl_s": {"min": 60, "max": 900}},
				"max_attempts_per_incident": 1,
				"cooldown_s": 300,
				"window_s": 3600,
				"max_per_window": 3,
				"targets": [{"id": "otel-traces", "pattern": false, "autonomy": "auto"}],
			},
			"quarantine_device": {
				"kind": "quarantine",
				"params": {},
				"max_attempts_per_incident": 1,
				"cooldown_s": 600,
				"window_s": 3600,
				"max_per_window": 1,
				"targets": [{"id": "device:[A-Za-z0-9_.\\-]{1,64}", "pattern": true, "autonomy": "plan_only"}],
			},
		},
	},
}

base := {
	"phase": "request",
	"actor": "system:aiops-remediation",
	"action": {"id": "restart_container", "target": "aiops-edge-runtime", "params": {}},
	"incident": {"status": "open"},
	"history": {
		"attempts_for_action": 0,
		"attempts_for_incident": 0,
		"attempts_on_target_in_window": 0,
		"seconds_since_last_attempt_on_target": null,
		"in_flight_total": 0,
	},
	"approval": null,
}

decide(facts) := result if {
	result := remediation.decision with data.aiops.model as fixture with input as facts
}

with_history(field, value) := object.union(base, {"history": object.union(base.history, {field: value})})

test_a_registered_auto_action_on_a_listed_target_is_approved if {
	d := decide(base)
	d.decision == "approved"
	d.reason == "permit"
	d.autonomy == "auto"
	d.policy_version == "test"
}

test_malformed_input_is_denied_not_guessed_at if {
	decide(object.remove(base, ["history"])).reason == "malformed_input"
	decide(object.union(base, {"phase": "later"})).reason == "malformed_input"
	decide(object.union(base, {"approval": {"by": 1, "role": "operator"}})).reason == "malformed_input"
	decide(object.remove(base, ["approval"])).reason == "malformed_input"
	decide(object.union(base, {"action": {"id": "restart_container", "target": 7, "params": {}}})).reason == "malformed_input"
}

test_only_the_remediation_identity_may_ask if {
	d := decide(object.union(base, {"actor": "operator:alice"}))
	d.decision == "denied"
	d.reason == "wrong_actor"
}

test_an_unregistered_action_is_denied if {
	decide(object.union(base, {"action": {"id": "format_disk", "target": "aiops-edge-runtime", "params": {}}})).reason == "unknown_action"
}

test_a_target_not_listed_for_the_action_is_denied if {
	decide(object.union(base, {"action": {"id": "restart_container", "target": "aiops-grafana", "params": {}}})).reason == "target_not_registered"
}

test_a_pattern_target_matches_only_whole_values if {
	ok := object.union(base, {"action": {"id": "quarantine_device", "target": "device:cam-01", "params": {}}})
	decide(ok).decision == "plan_only"
	decide(object.union(base, {"action": {"id": "quarantine_device", "target": "device:cam 01", "params": {}}})).reason == "target_not_registered"
	decide(object.union(base, {"action": {"id": "quarantine_device", "target": "xdevice:cam-01", "params": {}}})).reason == "target_not_registered"
	decide(object.union(base, {"action": {"id": "quarantine_device", "target": "device:cam-01;rm", "params": {}}})).reason == "target_not_registered"
}

sampling(params) := object.union(base, {"action": {"id": "set_trace_sampling", "target": "otel-traces", "params": params}})

test_parameters_inside_their_bounds_are_accepted if {
	decide(sampling({"ratio": 0.1, "ttl_s": 600})).decision == "approved"
	decide(sampling({"ratio": 0.01, "ttl_s": 60})).decision == "approved"
	decide(sampling({"ratio": 0.5, "ttl_s": 900})).decision == "approved"
}

test_parameters_outside_their_bounds_are_denied_at_both_ends if {
	decide(sampling({"ratio": 0.005, "ttl_s": 600})).reason == "parameter_out_of_bounds"
	decide(sampling({"ratio": 0.9, "ttl_s": 600})).reason == "parameter_out_of_bounds"
	decide(sampling({"ratio": 0.1, "ttl_s": 86400})).reason == "parameter_out_of_bounds"
	decide(sampling({"ratio": "0.1", "ttl_s": 600})).reason == "parameter_out_of_bounds"
}

test_missing_and_unknown_parameters_are_denied if {
	decide(sampling({"ratio": 0.1})).reason == "parameter_missing"
	decide(sampling({"ratio": 0.1, "ttl_s": 600, "extra": 1})).reason == "parameter_not_allowed"
	decide(object.union(base, {"action": {"id": "restart_container", "target": "aiops-edge-runtime", "params": {"force": true}}})).reason == "parameter_not_allowed"
}

test_a_resolved_incident_has_nothing_to_remediate if {
	decide(object.union(base, {"incident": {"status": "resolved"}})).reason == "incident_not_live"
}

test_a_person_owning_the_incident_stops_automation if {
	decide(object.union(base, {"incident": {"status": "acknowledged"}})).reason == "incident_held_by_a_person"
	decide(object.union(base, {"incident": {"status": "escalated"}})).reason == "incident_held_by_a_person"
	decide(object.union(base, {"incident": {"status": "reopened"}})).decision == "approved"
}

test_the_per_incident_budget_is_a_hard_stop if {
	decide(with_history("attempts_for_incident", 3)).decision == "approved"
	decide(with_history("attempts_for_incident", 4)).reason == "incident_attempt_budget_spent"
}

test_an_action_is_not_retried_beyond_its_own_limit if {
	decide(with_history("attempts_for_action", 1)).decision == "approved"
	decide(with_history("attempts_for_action", 2)).reason == "action_attempts_spent"
}

test_the_cooldown_holds_until_it_has_passed if {
	decide(with_history("seconds_since_last_attempt_on_target", 30)).reason == "cooldown"
	decide(with_history("seconds_since_last_attempt_on_target", 119.9)).reason == "cooldown"
	decide(with_history("seconds_since_last_attempt_on_target", 120)).decision == "approved"
}

test_a_target_is_rate_limited_over_its_window if {
	decide(with_history("attempts_on_target_in_window", 2)).decision == "approved"
	decide(with_history("attempts_on_target_in_window", 3)).reason == "rate_limited"
}

test_only_one_remediation_may_be_in_flight if {
	decide(with_history("in_flight_total", 1)).reason == "busy"
	decide(with_history("in_flight_total", 0)).decision == "approved"
}

test_the_request_being_executed_is_the_one_in_flight if {
	execute := object.union(base, {"phase": "execute", "history": object.union(base.history, {"in_flight_total": 1})})
	decide(execute).decision == "approved"
	decide(object.union(execute, {"history": object.union(execute.history, {"in_flight_total": 2})})).reason == "busy"
}

test_the_budgets_that_applied_to_the_request_do_not_block_the_approved_request_from_running if {
	execute := object.union(base, {"phase": "execute", "history": {
		"attempts_for_action": 2,
		"attempts_for_incident": 4,
		"attempts_on_target_in_window": 3,
		"seconds_since_last_attempt_on_target": 1,
		"in_flight_total": 1,
	}})
	decide(execute).decision == "approved"
}

postgres := object.union(base, {"action": {"id": "restart_container", "target": "aiops-postgres", "params": {}}})

test_a_stateful_target_needs_a_person_first if {
	d := decide(postgres)
	d.decision == "needs_approval"
	d.autonomy == "approval"
}

test_an_approved_stateful_restart_runs_and_names_the_approval if {
	d := decide(object.union(postgres, {"approval": {"by": "supervisor:bob", "role": "supervisor"}}))
	d.decision == "approved"
	d.reason == "permit_with_approval"
}

test_the_wrong_role_cannot_approve if {
	decide(object.union(postgres, {"approval": {"by": "dispatcher:dan", "role": "dispatcher"}})).reason == "approver_not_permitted"
}

test_the_remediation_identity_cannot_approve_its_own_request if {
	decide(object.union(postgres, {"approval": {"by": "system:aiops-remediation", "role": "operator"}})).reason == "approver_is_requester"
}

test_a_needless_approval_on_an_auto_target_changes_nothing if {
	d := decide(object.union(base, {"approval": {"by": "supervisor:bob", "role": "supervisor"}}))
	d.reason == "permit"
}

test_a_plan_only_action_is_never_approved_whoever_approves_it if {
	quarantine := object.union(base, {"action": {"id": "quarantine_device", "target": "device:cam-01", "params": {}}})
	decide(quarantine).decision == "plan_only"
	decide(object.union(quarantine, {"approval": {"by": "supervisor:bob", "role": "supervisor"}})).decision == "plan_only"
}

test_a_denial_beats_plan_only_and_needs_approval if {
	quarantine := object.union(base, {"action": {"id": "quarantine_device", "target": "device:cam-01", "params": {}}, "incident": {"status": "acknowledged"}})
	decide(quarantine).reason == "incident_held_by_a_person"
	decide(object.union(postgres, {"incident": {"status": "resolved"}})).reason == "incident_not_live"
}

test_the_lowest_rank_decides_when_several_things_are_wrong if {
	facts := object.union(with_history("in_flight_total", 5), {"actor": "operator:alice", "incident": {"status": "resolved"}})
	decide(facts).reason == "wrong_actor"
}
