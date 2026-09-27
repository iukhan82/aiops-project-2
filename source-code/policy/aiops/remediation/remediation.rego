# P10.08: which platform remediation the AIOps worker may run, on what, how much, how often.
#
# One question is answered here: may THIS action, on THIS target, with THESE parameters, be run now for THIS incident?
#
#   input = {
#     "actor":     who is asking ("system:aiops-remediation" for the worker),
#     "action":    {"id", "target", "params"},
#     "incident":  {"status"} (the platform incident the action is for),
#     "history":   {"attempts_for_action", "attempts_for_incident", "attempts_on_target_in_window",
#                   "seconds_since_last_attempt_on_target" (number or null), "in_flight_total"} - counted by the platform
#                  from its own records, never supplied by the caller's opinion,
#     "approval":  null, or {"by", "role"} when a person has approved,
#     "phase":     "request" (may this be started?) or "execute" (may the approved request run now?)
#   }
#
# The registry of actions, their targets, autonomy levels, parameter bounds, cooldowns, windows and budgets is DATA
# (aiops/model/data.json -> remediation), generated from backend/aiops/remediation.py by policy/build_data.py. Nothing
# about a particular action is written here.
#
# Outcomes: "approved" (run it), "needs_approval" (a person must approve first), "plan_only" (software must never run it;
# record the plan and escalate), "denied". Checks run in a fixed order and the FIRST failure decides, so the reason a person
# reads is stable. Malformed input is the lowest rank and denies. An engine that is down or returns nothing is not "deny"
# either: the worker treats it as policy_unavailable and does nothing (backend/pdp.py).
package aiops.remediation

import rego.v1

model := data.aiops.model

reg := model.remediation

action := reg.actions[input.action.id]

# ---------------------------------------------------------------- input shape

nullable_number(value) if is_null(value)

nullable_number(value) if is_number(value)

nullable_string(value) if is_null(value)

nullable_string(value) if is_string(value)

default valid_input := false

valid_input if {
	input.phase in {"request", "execute"}
	is_string(input.actor)
	is_string(input.action.id)
	is_string(input.action.target)
	is_object(input.action.params)
	is_string(input.incident.status)
	is_number(input.history.attempts_for_action)
	is_number(input.history.attempts_for_incident)
	is_number(input.history.attempts_on_target_in_window)
	nullable_number(input.history.seconds_since_last_attempt_on_target)
	is_number(input.history.in_flight_total)
	approval_ok
}

approval_ok if is_null(input.approval)

approval_ok if {
	is_string(input.approval.by)
	is_string(input.approval.role)
}

# ---------------------------------------------------------------- target and parameters

target_matches(t) if {
	not t.pattern
	t.id == input.action.target
}

target_matches(t) if {
	t.pattern
	regex.match(sprintf("^(?:%s)$", [t.id]), input.action.target)
}

matched_targets := [t |
	some t in action.targets
	target_matches(t)
]

autonomy := matched_targets[0].autonomy

unknown_params := {name |
	some name, _ in input.action.params
	not action.params[name]
}

missing_params := {name |
	some name, _ in action.params
	not input.action.params[name]
}

out_of_bounds contains name if {
	some name, _ in action.params
	value := input.action.params[name]
	not is_number(value)
}

out_of_bounds contains name if {
	some name, bounds in action.params
	value := input.action.params[name]
	is_number(value)
	value < bounds.min
}

out_of_bounds contains name if {
	some name, bounds in action.params
	value := input.action.params[name]
	is_number(value)
	value > bounds.max
}

# ---------------------------------------------------------------- violations (lowest rank decides)

violations contains {
	"rank": 1,
	"code": "malformed_input",
	"message": "the policy input is malformed, so the action is refused rather than guessed at",
} if not valid_input

violations contains {
	"rank": 10,
	"code": "wrong_actor",
	"message": sprintf("'%s' may not run platform remediation: only '%s' holds that authority", [input.actor, reg.actor]),
} if {
	valid_input
	input.actor != reg.actor
}

violations contains {
	"rank": 20,
	"code": "unknown_action",
	"message": sprintf("'%s' is not a registered remediation action", [input.action.id]),
} if {
	valid_input
	not action
}

violations contains {
	"rank": 30,
	"code": "target_not_registered",
	"message": sprintf("'%s' is not a registered target of '%s'", [input.action.target, input.action.id]),
} if {
	valid_input
	action
	count(matched_targets) == 0
}

violations contains {
	"rank": 40,
	"code": "parameter_not_allowed",
	"message": sprintf("parameters %v are not accepted by '%s'", [sort(unknown_params), input.action.id]),
} if {
	valid_input
	action
	count(unknown_params) > 0
}

violations contains {
	"rank": 42,
	"code": "parameter_missing",
	"message": sprintf("'%s' requires the parameters %v", [input.action.id, sort(missing_params)]),
} if {
	valid_input
	action
	count(missing_params) > 0
}

violations contains {
	"rank": 45,
	"code": "parameter_out_of_bounds",
	"message": sprintf("parameters %v are outside the bounds registered for '%s'", [sort(out_of_bounds), input.action.id]),
} if {
	valid_input
	action
	count(out_of_bounds) > 0
}

violations contains {
	"rank": 50,
	"code": "incident_not_live",
	"message": sprintf("the incident is '%s': there is nothing left to remediate", [input.incident.status]),
} if {
	valid_input
	input.incident.status == "resolved"
}

violations contains {
	"rank": 60,
	"code": "incident_held_by_a_person",
	"message": sprintf("the incident is '%s': a person owns it, automation stands down", [input.incident.status]),
} if {
	valid_input
	input.incident.status in reg.held_by_person_statuses
}

violations contains {
	"rank": 70,
	"code": "incident_attempt_budget_spent",
	"message": sprintf("%d attempts have already been made for this incident (limit %d)", [input.history.attempts_for_incident, reg.max_attempts_per_incident]),
} if {
	valid_input
	input.phase == "request"
	input.history.attempts_for_incident >= reg.max_attempts_per_incident
}

violations contains {
	"rank": 80,
	"code": "action_attempts_spent",
	"message": sprintf("'%s' has already been tried %d time(s) on this incident (limit %d)", [input.action.id, input.history.attempts_for_action, action.max_attempts_per_incident]),
} if {
	valid_input
	action
	input.phase == "request"
	input.history.attempts_for_action >= action.max_attempts_per_incident
}

violations contains {
	"rank": 90,
	"code": "cooldown",
	"message": sprintf("the last attempt on '%s' was %d s ago; the cooldown is %d s", [input.action.target, input.history.seconds_since_last_attempt_on_target, action.cooldown_s]),
} if {
	valid_input
	action
	input.phase == "request"
	is_number(input.history.seconds_since_last_attempt_on_target)
	input.history.seconds_since_last_attempt_on_target < action.cooldown_s
}

violations contains {
	"rank": 100,
	"code": "rate_limited",
	"message": sprintf("'%s' has had %d attempts in the last %d s (limit %d)", [input.action.target, input.history.attempts_on_target_in_window, action.window_s, action.max_per_window]),
} if {
	valid_input
	action
	input.phase == "request"
	input.history.attempts_on_target_in_window >= action.max_per_window
}

# In the request phase nothing of ours may be in flight; in the execute phase the request itself is the one in flight.
violations contains {
	"rank": 110,
	"code": "busy",
	"message": sprintf("%d remediation(s) already in flight; the platform allows %d at a time", [input.history.in_flight_total, reg.max_in_flight]),
} if {
	valid_input
	input.phase == "request"
	input.history.in_flight_total >= reg.max_in_flight
}

violations contains {
	"rank": 110,
	"code": "busy",
	"message": sprintf("%d remediations in flight; the platform allows %d at a time", [input.history.in_flight_total, reg.max_in_flight]),
} if {
	valid_input
	input.phase == "execute"
	input.history.in_flight_total > reg.max_in_flight
}

violations contains {
	"rank": 120,
	"code": "approver_not_permitted",
	"message": sprintf("role '%s' may not approve platform remediation", [input.approval.role]),
} if {
	valid_input
	action
	autonomy == "approval"
	not is_null(input.approval)
	not input.approval.role in reg.approver_roles
}

violations contains {
	"rank": 125,
	"code": "approver_is_requester",
	"message": "the requester cannot approve its own remediation",
} if {
	valid_input
	action
	autonomy == "approval"
	not is_null(input.approval)
	input.approval.by == reg.actor
}

lowest_rank := min({v.rank | some v in violations})

first_violation := [v |
	some v in violations
	v.rank == lowest_rank
][0]

# ---------------------------------------------------------------- outcome

decision := {
	"decision": "denied",
	"reason": first_violation.code,
	"message": first_violation.message,
	"autonomy": null,
	"policy_version": model.version,
} if {
	count(violations) > 0
}

decision := {
	"decision": "plan_only",
	"reason": "plan_only",
	"message": "software may not run this action; the plan is recorded and the incident is escalated",
	"autonomy": autonomy,
	"policy_version": model.version,
} if {
	count(violations) == 0
	autonomy == "plan_only"
}

decision := {
	"decision": "needs_approval",
	"reason": "needs_approval",
	"message": "a person must approve this action before it runs",
	"autonomy": autonomy,
	"policy_version": model.version,
} if {
	count(violations) == 0
	autonomy == "approval"
	is_null(input.approval)
}

decision := {
	"decision": "approved",
	"reason": "permit",
	"message": null,
	"autonomy": autonomy,
	"policy_version": model.version,
} if {
	count(violations) == 0
	autonomy == "auto"
}

decision := {
	"decision": "approved",
	"reason": "permit_with_approval",
	"message": null,
	"autonomy": autonomy,
	"policy_version": model.version,
} if {
	count(violations) == 0
	autonomy == "approval"
	not is_null(input.approval)
}
