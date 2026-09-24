# P09.03: who may call which endpoint (the platform's policy decision point for the API).
#
# The API asks once per authenticated request. Input:
#   {"service": "api" | "scenario-control", "method": "GET", "path": "<route template>", "roles": ["operator", ...]}
# `path` is the route template ("/api/v1/incidents/{incident_id}"), never the concrete URL, so an identifier in the URL can never
# change the decision. The endpoint table and the role -> capability matrix are data (aiops/model/data.json), generated from the
# one UX inventory by policy/build_data.py; nothing about a route or a role is written here.
#
# Default deny: an endpoint that is not listed, a caller with no operating role, and a capability the caller's roles do not add
# up to are all denied. The decision carries a reason so a refusal is explainable and auditable.
package aiops.api.authz

import rego.v1

endpoint_key := sprintf("%s %s %s", [input.service, upper(input.method), input.path])

endpoint := data.aiops.model.endpoints[endpoint_key]

known_roles contains role if {
	some role in input.roles
	role in data.aiops.model.roles
}

held contains capability if {
	some capability, spec in data.aiops.model.capabilities
	some role in known_roles
	role in spec.roles
}

outcome := "unlisted_endpoint" if {
	not endpoint
} else := "public" if {
	endpoint.public
} else := "no_operating_role" if {
	count(known_roles) == 0
} else := "permit" if {
	endpoint.capability == null
} else := "permit" if {
	endpoint.capability in held
} else := "forbidden"

default allow := false

allow if outcome in {"public", "permit"}

default required_capability := null

required_capability := endpoint.capability

decision := {
	"allow": allow,
	"reason": outcome,
	"capability": required_capability,
	"policy_version": data.aiops.model.version,
}
