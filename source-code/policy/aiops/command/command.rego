# P09.03: command authority and the execution-time policy (`contracts/policy-decision/v1`).
#
# Two questions are answered here and nowhere else at run time:
#
#   authority - may one of these roles START (kind "request") or REVIEW (kind "review") a command of this action type?
#               Input: {"kind", "action_type", "critical_incident_on_target", "roles"}.
#               Output names the role that holds the authority, so the caller records the role that was actually used.
#
#   decision  - may this command be APPROVED (phase "approval") or EXECUTED (phase "execution")?
#               Input: the facts the platform gathered (does the target exist, is its evidence fresh, what state is the
#               recommendation in, who requested, who approved) - see backend/control/policy.py `context_to_input`.
#               Checks run in a fixed order and the FIRST failure decides, so the reason a person sees is stable.
#
# The safety class is DERIVED here from the action type and whether an active critical incident references the target; a
# caller cannot supply it. The role tables, the action -> class table and the adapter registry are data (aiops/model/data.json),
# generated from backend/roles.py and backend/control/policy.py by policy/build_data.py.
#
# Fail closed: every rule that can fire on a missing or malformed field is guarded by `valid_input`, which is rule rank 1, so an
# input that lacks anything a later rule needs is denied - never approved by an undefined comparison. An engine that is down or
# returns nothing is not "deny" either: the caller treats it as policy_unavailable and the command waits.
package aiops.command

import rego.v1

model := data.aiops.model

# ---------------------------------------------------------------- safety class

safety_class := "SC-2" if {
	input.critical_incident_on_target == true
} else := model.safety_class_of_action[input.action_type] if {
	true
} else := "SC-1"

# ---------------------------------------------------------------- authority

authority_table := {
	"request": model.request_roles,
	"review": model.approve_roles,
	"override": model.override_roles,
}

permitted_roles := authority_table[input.kind][safety_class]

authorised_roles := sort([role |
	some role in input.roles
	role in permitted_roles
])

default authority := {
	"allow": false,
	"role": null,
	"safety_class": null,
	"reason": "malformed_authority_input",
	"policy_version": null,
}

authority := {
	"allow": count(authorised_roles) > 0,
	"role": role,
	"safety_class": safety_class,
	"reason": authority_reason,
	"policy_version": model.version,
} if {
	is_string(input.action_type)
	is_boolean(input.critical_incident_on_target)
	is_array(input.roles)
	input.kind in {"request", "review", "override"}
	role := authorised_role
}

default authorised_role := null

authorised_role := authorised_roles[0]

authority_reason := "permit" if {
	count(authorised_roles) > 0
} else := sprintf("role_cannot_%s", [input.kind])

# ---------------------------------------------------------------- decision

expires_ns := time.parse_rfc3339_ns(input.expires_at)

now_ns := time.parse_rfc3339_ns(input.now)

nullable_string(value) if is_null(value)

nullable_string(value) if is_string(value)

default common_input_ok := false

common_input_ok if {
	input.phase in {"approval", "execution"}
	is_string(input.action_type)
	is_boolean(input.critical_incident_on_target)
	is_string(input.target.adapter)
	is_string(input.target.entity_id)
	is_boolean(input.target.exists)
	is_boolean(input.target.evidence_fresh)
	is_string(input.requester.id)
	nullable_string(input.requester.role)
	is_number(expires_ns)
	is_number(now_ns)
}

default approval_input_ok := false

approval_input_ok if {
	input.phase == "approval"
	is_string(input.approver.id)
	nullable_string(input.approver.role)
	recommendation_input_ok
}

approval_input_ok if {
	input.phase == "execution"
	is_string(input.actor)
	is_string(input.command_status)
	nullable_string(input.approver.id)
	nullable_string(input.approver.role)
}

default recommendation_input_ok := false

recommendation_input_ok if is_null(input.recommendation)

recommendation_input_ok if {
	is_string(input.recommendation.id)
	nullable_string(input.recommendation.action_type)
	nullable_string(input.recommendation.status)
}

valid_input if {
	common_input_ok
	approval_input_ok
}

default valid_input := false

# Each violation carries a rank; the lowest rank present is the decision.
violations contains {
	"rank": 1,
	"decision": "denied",
	"error_code": "policy_denied",
	"message": "the policy input is malformed, so the command is refused rather than guessed at",
} if not valid_input

violations contains {
	"rank": 3,
	"decision": "denied",
	"error_code": "policy_denied",
	"message": sprintf("'%s' may not execute a command: only '%s' holds EXECUTE authority", [input.actor, model.executor]),
} if {
	valid_input
	input.phase == "execution"
	input.actor != model.executor
}

violations contains {
	"rank": 4,
	"decision": "denied",
	"error_code": "policy_denied",
	"message": sprintf("command is '%s', not 'approved' - refusing to execute", [input.command_status]),
} if {
	valid_input
	input.phase == "execution"
	input.command_status != "approved"
}

violations contains {
	"rank": 15,
	"decision": "denied",
	"error_code": "policy_denied",
	"message": "no approval is recorded for this command",
} if {
	valid_input
	input.phase == "execution"
	is_null(input.approver.id)
}

violations contains {
	"rank": 20,
	"decision": "expired",
	"error_code": "expired",
	"message": sprintf("command expired at %s", [input.expires_at]),
} if {
	valid_input
	now_ns >= expires_ns
}

violations contains {
	"rank": 30,
	"decision": "denied",
	"error_code": "policy_denied",
	"message": "the requester cannot also approve their own command (four-eyes required)",
} if {
	valid_input
	safety_class in model.four_eyes_classes
	input.approver.id == input.requester.id
}

violations contains {
	"rank": 40,
	"decision": "denied",
	"error_code": "policy_denied",
	"message": sprintf("role %s may not approve a %s action ('%s')", [quoted(input.approver.role), safety_class, input.action_type]),
} if {
	valid_input
	not input.approver.role in model.approve_roles[safety_class]
}

violations contains {
	"rank": 50,
	"decision": "denied",
	"error_code": "policy_denied",
	"message": sprintf("role %s may not request a %s action ('%s')", [quoted(input.requester.role), safety_class, input.action_type]),
} if {
	valid_input
	not is_null(input.requester.role)
	not input.requester.role in model.request_roles[safety_class]
}

violations contains {
	"rank": 60,
	"decision": "denied",
	"error_code": "invalid_target",
	"message": sprintf("unknown adapter '%s'", [input.target.adapter]),
} if {
	valid_input
	not model.adapter_target_kind[input.target.adapter]
}

violations contains {
	"rank": 70,
	"decision": "denied",
	"error_code": "invalid_target",
	"message": sprintf("'%s' is not a real, currently registered target for '%s'", [input.target.entity_id, input.target.adapter]),
} if {
	valid_input
	not input.target.exists
}

violations contains {
	"rank": 80,
	"decision": "denied",
	"error_code": "invalid_target",
	"message": sprintf("recommendation %s does not exist", [input.recommendation.id]),
} if {
	valid_input
	input.phase == "approval"
	not is_null(input.recommendation)
	is_null(input.recommendation.status)
}

violations contains {
	"rank": 90,
	"decision": "denied",
	"error_code": "policy_denied",
	"message": "command action_type does not match its recommendation's action_type",
} if {
	valid_input
	input.phase == "approval"
	not is_null(input.recommendation)
	not is_null(input.recommendation.status)
	input.recommendation.action_type != input.action_type
}

violations contains {
	"rank": 100,
	"decision": "denied",
	"error_code": "policy_denied",
	"message": sprintf("recommendation is '%s', no longer actionable", [input.recommendation.status]),
} if {
	valid_input
	input.phase == "approval"
	not is_null(input.recommendation)
	not is_null(input.recommendation.status)
	not input.recommendation.status in model.actionable_recommendation_statuses
}

violations contains {
	"rank": 110,
	"decision": "denied",
	"error_code": "stale_evidence",
	"message": sprintf("live evidence for '%s' is not fresh (SAFE-03)", [input.target.entity_id]),
} if {
	valid_input
	not input.target.evidence_fresh
}

quoted(value) := sprintf("'%s'", [value]) if is_string(value)

quoted(value) := "None" if is_null(value)

lowest_rank := min({v.rank | some v in violations})

first_violation := [v |
	some v in violations
	v.rank == lowest_rank
][0]

decision := {
	"decision": first_violation.decision,
	"error_code": first_violation.error_code,
	"message": first_violation.message,
	"safety_class": safety_class,
	"policy_version": model.version,
} if {
	count(violations) > 0
}

decision := {
	"decision": "approved",
	"error_code": null,
	"message": null,
	"safety_class": safety_class,
	"policy_version": model.version,
} if {
	count(violations) == 0
}
