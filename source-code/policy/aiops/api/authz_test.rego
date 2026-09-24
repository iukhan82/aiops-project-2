package aiops.api.authz_test

import data.aiops.api.authz
import rego.v1

fixture := {
	"version": "test",
	"roles": ["operator", "auditor"],
	"capabilities": {
		"map.view": {"roles": ["operator", "auditor"]},
		"commands.request": {"roles": ["operator"]},
	},
	"endpoints": {
		"api GET /api/v1/health": {"capability": null, "public": true},
		"api GET /api/v1/me": {"capability": null, "public": false},
		"api GET /api/v1/map": {"capability": "map.view", "public": false},
		"api POST /api/v1/commands": {"capability": "commands.request", "public": false},
	},
}

decide(service, method, path, roles) := result if {
	result := authz.decision with data.aiops.model as fixture
		with input as {"service": service, "method": method, "path": path, "roles": roles}
}

test_public_endpoint_needs_no_role if {
	d := decide("api", "GET", "/api/v1/health", [])
	d.allow
	d.reason == "public"
}

test_listed_endpoint_with_held_capability_is_permitted if {
	d := decide("api", "GET", "/api/v1/map", ["auditor"])
	d.allow
	d.capability == "map.view"
}

test_missing_capability_is_forbidden if {
	d := decide("api", "POST", "/api/v1/commands", ["auditor"])
	not d.allow
	d.reason == "forbidden"
	d.capability == "commands.request"
}

test_unlisted_endpoint_is_denied_by_default if {
	d := decide("api", "GET", "/api/v1/nothing", ["operator"])
	not d.allow
	d.reason == "unlisted_endpoint"
}

test_method_is_part_of_the_key if {
	d := decide("api", "DELETE", "/api/v1/map", ["operator"])
	not d.allow
	d.reason == "unlisted_endpoint"
}

test_service_is_part_of_the_key if {
	d := decide("scenario-control", "GET", "/api/v1/map", ["operator"])
	not d.allow
	d.reason == "unlisted_endpoint"
}

test_caller_without_a_known_role_is_denied_even_where_no_capability_is_needed if {
	d := decide("api", "GET", "/api/v1/me", ["made_up_role"])
	not d.allow
	d.reason == "no_operating_role"
}

test_any_known_role_may_call_an_endpoint_that_needs_no_capability if {
	d := decide("api", "GET", "/api/v1/me", ["auditor"])
	d.allow
}

test_unknown_roles_add_nothing if {
	d := decide("api", "POST", "/api/v1/commands", ["made_up_role", "auditor"])
	not d.allow
}

test_lowercase_method_is_normalised if {
	d := decide("api", "get", "/api/v1/map", ["operator"])
	d.allow
}

test_missing_input_is_a_denial_not_an_undefined_decision if {
	d := authz.decision with data.aiops.model as fixture with input as {}
	not d.allow
}

test_decision_names_the_policy_version if {
	d := decide("api", "GET", "/api/v1/map", ["operator"])
	d.policy_version == "test"
}
