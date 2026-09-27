"""What Phase 12 says about the platform: the acceptance scenarios, the workflows by role, the faults, and the safety, privacy, accessibility and fairness rows (P12.01, P12.02, P12.04).

Every row stands on named checks of named evidence (see `matrix.py`). A row that claims `partial` or `gap` says what is not shown and who owns it - those are the platform's known limits, listed
here rather than hidden. The owners are role codes and tasks of the register; P12.05 is where an open row is resolved or accepted.
"""

from __future__ import annotations

from acceptance.matrix import Cite as C
from acceptance.matrix import Row as R

# ================================================================================================ P12.01 - the seven acceptance scenarios (PROJECT_PLAN.md section 10)
SCENARIOS: tuple[R, ...] = (
    # ---- 1. peak congestion
    R(
        "S1.1",
        "Peak congestion: queue spillback is detected from the live network state",
        (
            C("p06_04_congestion", "blockage_run_yields_congestion_and_spillback_candidates"),
            C("p06_04_congestion", "spillback_reported_separately_from_congestion"),
            C("p06_04_congestion", "candidates_carry_location_severity_onset_and_confidence"),
        ),
        links=("RQ25", "ACC-03"),
    ),
    R(
        "S1.2",
        "Peak congestion: the near-future queue is predicted",
        (
            C(
                "p06_03_forecast_model",
                "served_forecasts_can_be_scored_against_the_kpi_that_actually_followed",
            ),
            C(
                "p06_03_forecast_model",
                "targets_rejected_on_validation_are_served_by_the_baseline_and_labelled_so",
            ),
            C("p06_03_forecast_model", "every_forecast_validates_as_forecast_v1"),
        ),
        status="partial",
        limit="the trained forecast is significantly worse than the baseline on the held-out test for the 30-minute density target (ACC-02), so the baseline is what is served there: the prediction is measured, labelled and honest, but it is a weak predictor.",
        owner="DATA/OPS - P12.05 (finding F-02)",
        links=("ACC-02",),
    ),
    R(
        "S1.3",
        "Peak congestion: a corridor plan is recommended with benefit, harm and safe bounds",
        (
            C("p07_04_recommendations", "signal_generator_uses_the_real_measured_corridor_delay"),
            C(
                "p07_04_recommendations",
                "pedestrian_clearance_is_preserved_on_every_signal_alternative",
            ),
            C(
                "p07_04_recommendations",
                "no_alternative_exceeding_the_signal_deviation_bound_survives_enforcement",
            ),
        ),
        links=("RQ25",),
    ),
    R(
        "S1.4",
        "Peak congestion: a person approves it, a second person, after the policy runs again on what is true now",
        (
            C(
                "p08_08_command_workflow_api",
                "a_supervisor_approves_after_the_policy_runs_again_against_what_is_true_now",
            ),
            C("p08_08_command_workflow_api", "the_requester_cannot_approve_their_own_command"),
            C("p08_08_ui_actions", "approval is a confirmation that restates everything"),
        ),
        links=("RQ25",),
    ),
    R(
        "S1.5",
        "Peak congestion: the approved plan is applied to the simulator by the only identity that may execute",
        (
            C(
                "p07_06_simulator_adapters",
                "the_signal_adapter_actually_moved_a_real_traffic_lights_next_switch",
            ),
            C(
                "p08_08_command_workflow_api",
                "the_executor_service_picks_up_approved_commands_and_records_what_the_adapter_observed",
            ),
            C(
                "p08_08_command_workflow_api",
                "there_is_no_way_for_a_person_to_execute_a_command_through_the_api",
            ),
        ),
        links=("RQ25",),
    ),
    R(
        "S1.6",
        "Peak congestion: the improvement is measured independently, and reported as it is",
        (
            C(
                "p07_09_outcomes",
                "a_real_signal_extension_within_the_noise_band_is_ineffective_and_not_rolled_back",
            ),
            C("p07_09_outcomes", "a_paired_transit_priority_run_is_effective_and_not_rolled_back"),
            C("p07_09_outcomes", "kpi_windows_are_read_from_the_platforms_own_corridor_kpis"),
        ),
        detail="the verifier measures before and after windows; a signal extension inside the noise band is reported as ineffective, not as an improvement",
        links=("RQ25",),
    ),
    # ---- 2. collision response
    R(
        "S2.1",
        "Collision: safety candidates are raised from telemetry and corroborated by a second source",
        (
            C("p06_05_safety", "all_four_overlay_kinds_detected_from_the_database"),
            C("p06_05_safety", "corroboration_by_a_second_source_raises_confidence"),
            C("p06_05_safety", "every_candidate_cites_events_that_exist"),
        ),
        links=("RQ25",),
    ),
    R(
        "S2.2",
        "Collision: candidates from independent sensor modalities are correlated into one explainable incident",
        (
            C("p06_07_incidents", "B2_a_confident_candidate_opens_an_incident"),
            C("p06_07_incidents", "correlation_reduces_candidates_to_fewer_incidents"),
            C(
                "p06_07_incidents",
                "B3_two_independent_sensor_modalities_escalate_a_high_severity_incident_immediately",
            ),
            C(
                "p06_07_incidents",
                "every_live_incident_has_ranked_hypotheses_and_none_claims_a_verified_cause",
            ),
        ),
        status="partial",
        limit="the correlator joins sensor modalities (loop, camera, road condition); an emergency CALL is not an input to it (calls are dispatched, not correlated), so 'video, radar and call evidence' are not fused into one incident - a person links a call to an incident.",
        owner="ARCH/EMERG - P12.05 (finding F-05)",
        links=("RQ25",),
    ),
    R(
        "S2.3",
        "Collision: the affected lanes are closed through the governed adapter",
        (
            C(
                "p07_06_simulator_adapters",
                "the_diversion_adapter_actually_closed_the_real_general_traffic_lanes",
            ),
            C(
                "p07_04_recommendations",
                "diversion_route_avoids_the_closed_segment_because_it_reuses_the_real_router",
            ),
        ),
        links=("RQ25",),
    ),
    R(
        "S2.4",
        "Collision: units are dispatched with routes, ETA and uncertainty, and the call follows its unit to cleared",
        (
            C("p07_01_emergency", "every_assignment_has_five_transitions_assigned_through_clear"),
            C(
                "p08_07_operator_actions_api",
                "assigning_a_unit_finds_routes_from_where_the_unit_is",
            ),
            C(
                "p08_07_operator_actions_api",
                "the_call_follows_its_unit_en_route_then_on_scene_then_cleared",
            ),
            C(
                "p07_10_scenarios",
                "every_incident_reroute_avoids_the_blocked_segment_it_learned_from_the_incident_record",
            ),
        ),
        links=("RQ25",),
    ),
    R(
        "S2.5",
        "Collision: a diversion is issued and a driver message is published to the sign",
        (
            C(
                "p07_04_recommendations",
                "diversion_generator_produces_alternatives_with_benefit_and_harm_from_the_real_router",
            ),
            C(
                "p07_06_simulator_adapters",
                "the_vms_adapter_completes_and_honestly_reports_no_simulator_actuation",
            ),
        ),
        status="partial",
        limit="the variable-message-sign adapter records the message and says so honestly: SUMO has no sign to actuate, so 'the warning was published' is a recorded command, not an observed change on the road.",
        owner="CONTROL - P12.05 (finding F-06)",
        links=("RQ25",),
    ),
    R(
        "S2.6",
        "Collision: traffic recovery is verified independently of the requester, approver and executor",
        (
            C(
                "p07_09_outcomes",
                "an_unsafe_outcome_on_a_non_actuating_adapter_is_rolled_back_with_the_no_physical_undo_stated",
            ),
            C(
                "p08_08_command_workflow_api",
                "the_verifier_records_an_outcome_for_the_executed_command_independent_of_requester_approver_and_executor",
            ),
            C("p07_10_scenarios", "general_traffic_effect_was_measured_paired_and_is_reported"),
        ),
        links=("RQ25",),
    ),
    # ---- 3. ambulance priority
    R(
        "S3.1",
        "Ambulance: a fastest-safe route with ETA and uncertainty from live state, never through a closed segment",
        (
            C(
                "p07_02_routing",
                "the_router_never_uses_a_closed_segment_and_the_grid_still_finds_a_way_through",
            ),
            C("p07_02_routing", "a_kpi_window_older_than_the_live_budget_is_not_used"),
            C("p07_02_routing", "multiple_genuinely_different_alternatives_exist_on_the_real_grid"),
        ),
        links=("RQ25",),
    ),
    R(
        "S3.2",
        "Ambulance: signals are pre-empted along the route with pedestrian clearance preserved",
        (
            C(
                "p07_07_preemption",
                "signal_safety_monitor_reports_zero_violations_during_preemption",
            ),
            C(
                "p07_07_preemption",
                "abort_restores_every_touched_intersection_to_its_own_original_program",
            ),
            C(
                "p07_10_scenarios",
                "safe_01_zero_signal_safety_violations_over_every_step_of_every_platform_run",
            ),
        ),
        links=("RQ25", "SAFE-01"),
    ),
    R(
        "S3.3",
        "Ambulance: arrival time is compared with the same run without pre-emption",
        (
            C(
                "p07_10_scenarios",
                "eta_03_pre_emption_reduces_travel_time_on_average_and_never_worsens_a_pair_beyond_the_outcome_tolerance",
            ),
            C("p07_10_scenarios", "eta_03_the_benefit_is_not_explained_by_chance_sign_test"),
            C("p07_10_scenarios", "eta_01_pooled_mae_within_15_percent_in_normal_traffic"),
        ),
        links=("ETA-01", "ETA-03"),
    ),
    R(
        "S3.4",
        "Ambulance: staged multi-agency response holds units back until their prerequisite has arrived",
        (
            C(
                "p07_10_scenarios",
                "staged_response_holds_ems_back_until_fire_has_arrived_then_releases_it",
            ),
            C(
                "p07_03_cross_agency",
                "fire_and_ambulance_are_both_blocked_from_on_scene_before_their_real_prerequisite_arrives",
            ),
        ),
        links=("RQ25",),
    ),
    # ---- 4. road flooding
    R(
        "S4.1",
        "Flooding: a flood is detected from the road-condition sensor, corroborated by low friction, contradiction demoted",
        (
            C("p06_05_safety", "all_four_overlay_kinds_detected_from_the_database"),
            C("p06_05_safety", "overlay_specification_test_all_injected_detected_exactly"),
            C("p06_05_safety", "overlay_stuck_and_contradicted_flags_are_demoted_below_half"),
        ),
        status="partial",
        limit="the flood candidate combines the road-condition sensor's flood flag with its friction estimate; rainfall, water depth as a measurement, the speed reduction at loop detectors and reports from people are NOT combined into it.",
        owner="DATA/OPS - P12.05 (finding F-03)",
        links=("RQ25",),
    ),
    R(
        "S4.2",
        "Flooding: the flooded segment is treated as closed by routing and diversion",
        (
            C("p07_02_routing", "a_real_high_severity_incident_marks_its_segment_closed"),
            C(
                "p07_04_recommendations",
                "diversion_route_avoids_the_closed_segment_because_it_reuses_the_real_router",
            ),
            C(
                "p07_04_recommendations",
                "a_flooding_incident_on_a_corridor_gets_a_diversion_recommendation_that_avoids_every_segment_of_that_corridor",
            ),
        ),
        links=("RQ25",),
    ),
    R(
        "S4.3",
        "Flooding: a warning is published",
        (
            C(
                "p07_06_simulator_adapters",
                "the_vms_adapter_completes_and_honestly_reports_no_simulator_actuation",
            ),
        ),
        status="partial",
        limit="as S2.5: the sign command is recorded, the simulator shows nothing; no public-alert channel exists or is authorized.",
        owner="CONTROL - P12.05 (finding F-06)",
        links=("RQ25",),
    ),
    # ---- 5. signal failure
    R(
        "S5.1",
        "Signal failure: inconsistent phase or controller telemetry is detected and raises an incident",
        (C("p06_05_safety", "signal_fault_overlay_is_not_a_safety_candidate"),),
        status="gap",
        limit="a controller-reported fault is deliberately NOT a safety candidate, and nothing else raises it: the data-quality detector covers the four numeric device types, not the categorical signal state, and no rule looks for conflicting greens or a stuck phase in the controller's own telemetry. `signal_fault` is an incident type nothing produces.",
        owner="OPS/CONTROL - P12.05 (defect D-01)",
        links=("RQ25",),
    ),
    R(
        "S5.2",
        "Signal failure: the platform enters a safe plan",
        (),
        status="gap",
        limit="there is no safe-plan action among the governed adapters (signal plan change, diversion, sign, transit priority, pre-emption); a controller fault cannot be answered by the platform.",
        owner="CONTROL - P12.05 (defect D-01)",
        links=("RQ25",),
    ),
    R(
        "S5.3",
        "Signal failure: maintenance work is created",
        (),
        status="gap",
        limit="the platform has no maintenance work item; an escalated incident waits for a person (P10.08) but nothing is created for a maintenance crew.",
        owner="OPS - P12.05 (defect D-01)",
        links=("RQ25",),
    ),
    R(
        "S5.4",
        "Signal failure: restoration is verified",
        (
            C(
                "p07_09_outcomes",
                "a_post_action_telemetry_gap_is_unknown_and_escalated_never_defaulted_to_effective",
            ),
        ),
        status="partial",
        limit="the independent outcome verifier exists for governed actions and reports an unknown outcome honestly; there is no signal-failure action to verify.",
        owner="CONTROL - P12.05 (defect D-01)",
        links=("RQ25",),
    ),
    # ---- 6. communications outage
    R(
        "S6.1",
        "Outage: the edge buffers what it senses durably while the uplink is down and keeps working",
        (
            C(
                "p12_02_rec01_edge_outage",
                "the_readings_were_buffered_on_disk_while_the_uplink_was_down",
            ),
            C(
                "pytest:tests/test_edge_outbox.py",
                "test_uplink_outage_then_recovery_preserves_all_events_and_order",
            ),
            C(
                "pytest:tests/test_edge_runtime.py",
                "test_startup_with_verified_model_is_ready_and_not_degraded",
            ),
        ),
        links=("RQ02", "REC-01"),
    ),
    R(
        "S6.2",
        "Outage: stale state is shown as stale and never used as fresh",
        (
            C("p12_02_safe03_stale_state", "the_state_service_serves_a_fresh_reading_as_fresh"),
            C("p08_10_ui_acceptance", "stale data is never left looking live"),
            C("p08_10_ui_acceptance", "the API becoming unreachable is said plainly"),
        ),
        links=("SAFE-03", "UX-02"),
    ),
    R(
        "S6.3",
        "Outage: on reconnect the readings replay in order, acknowledged, without accepted duplicates",
        (
            C(
                "p12_02_rec01_edge_outage",
                "every_durably_buffered_reading_reached_the_database_none_missing",
            ),
            C("p12_02_rec01_edge_outage", "zero_accepted_duplicates_one_row_per_reading"),
            C("p12_02_rec01_edge_outage", "readings_replay_in_order"),
            C("p12_02_rec01_edge_outage", "every_reading_was_acknowledged_by_the_platform"),
        ),
        links=("REC-01",),
    ),
    R(
        "S6.4",
        "Outage: the central state is reconciled after the outage",
        (
            C("p05_05_ingestion", "replay_never_duplicates_the_row"),
            C("p05_05_ingestion", "full_topic_replay_from_new_consumer_group_is_idempotent"),
            C("p05_03_gateway", "outage_events_eventually_in_kafka"),
        ),
        links=("REC-01", "REC-02"),
    ),
    # ---- 7. bad model or configuration
    R(
        "S7.1",
        "Bad model: degraded predictions are detected as alerts on the running edge runtime",
        (
            C("p10_04_alert_rules", "live_EdgeModelErrors_fires_on_its_induced_fault"),
            C("p10_04_alert_rules", "live_EdgeModelInactive_fires_on_its_induced_fault"),
        ),
        links=("RQ07",),
    ),
    R(
        "S7.2",
        "Bad model: an unverified or tampered model is refused and the baseline serves",
        (
            C(
                "pytest:tests/test_edge_runtime.py",
                "test_tampered_model_is_refused_and_baseline_serves",
            ),
            C(
                "pytest:tests/test_edge_model_runtime.py",
                "test_flipped_byte_and_truncation_fail_integrity",
            ),
            C("p06_03_forecast_model", "tampered_or_unmanifested_package_is_refused"),
        ),
        links=("RQ06", "RQ07"),
    ),
    R(
        "S7.3",
        "Bad model: the model is rolled back to the previous verified version and health is verified",
        (
            C(
                "p10_09_recovery",
                "a_rotted_active_model_raises_the_model_alerts_and_the_incident_is_recovered_by_rolling_back_the_registry",
            ),
            C(
                "p10_09_recovery",
                "the_rollback_switched_the_pointer_to_the_previous_verified_version_and_restarted_the_runtime_which_then_served_it",
            ),
            C(
                "p10_09_recovery",
                "each_recovery_was_verified_from_the_alerts_staying_clear_and_only_then_was_the_incident_resolved",
            ),
        ),
        links=("RQ07", "REC-04"),
    ),
    R(
        "S7.4",
        "Bad configuration: a bad rollout is contained and undone",
        (
            C(
                "p11_05_target_recovery",
                "a_rollout_to_an_image_that_does_not_exist_leaves_the_old_api_serving_throughout",
            ),
            C(
                "p11_05_target_recovery",
                "rollout_undo_restores_the_previous_revision_and_the_api_is_healthy",
            ),
        ),
        links=("RQ22",),
    ),
)

# ================================================================================================ P12.01 - the workflow classes the acceptance names, for the roles that may perform them
# capability -> (positive evidence: the permitted role does it, negative evidence: the others are refused)
CAPABILITY_EVIDENCE: dict[str, tuple[tuple[C, ...], tuple[C, ...]]] = {
    "map.view": (
        (
            C("p08_05_ui_map", "draws the whole network"),
            C("p08_05_ui_map", "roles: incident and emergency layers"),
            C("p08_04_ui_auth", r"lands on its own home screen", 7),
        ),
        (
            C(
                "p08_04_auth_api",
                "the_api_enforces_exactly_the_inventorys_capability_for_every_implemented_get_endpoint_and_every_role",
            ),
            C("p08_04_auth_api", "the_live_websocket_needs_a_valid_token_with_map_view"),
        ),
    ),
    "analytics.view": (
        (
            C("p08_06_ui_analytics", "shows the latest complete window"),
            C("p08_06_ui_analytics", "summarises how many devices are reporting"),
        ),
        (
            C("p08_06_ui_analytics", "a role without analytics.view is refused"),
            C("p08_04_auth_api", "the_api_enforces_exactly_the_inventorys_capability"),
        ),
    ),
    "incidents.view": (
        (
            C("p08_07_ui_incident_dispatch", "the queue is critical first, filterable"),
            C("p08_07_ui_incident_dispatch", "the detail reads as evidence, not verdict"),
        ),
        (
            C("p08_04_auth_api", "the_api_enforces_exactly_the_inventorys_capability"),
            C(
                "p08_04_ui_auth",
                "a screen the role does not hold the capability for says which capability is missing",
            ),
        ),
    ),
    "incidents.manage": (
        (
            C("p08_07_operator_actions_api", "an_operator_acknowledges_an_incident"),
            C("p08_07_operator_actions_api", "an_incident_commander_resolves_with_a_note"),
            C("p08_07_operator_actions_api", "a_resolved_incident_can_be_reopened_by_a_supervisor"),
        ),
        (
            C(
                "p08_07_operator_actions_api",
                "roles_without_incidents_manage_are_forbidden_from_every_incident_action",
            ),
            C(
                "p08_07_ui_incident_dispatch",
                "a role that may read but not manage sees the incident with no action controls",
            ),
        ),
    ),
    "emergency.view": (
        (
            C(
                "p08_07_ui_incident_dispatch",
                "the call list shows priority, status and truth label",
            ),
            C("p08_07_ui_incident_dispatch", "the field view is read-only on a phone"),
        ),
        (C("p08_04_auth_api", "the_api_enforces_exactly_the_inventorys_capability"),),
    ),
    "emergency.dispatch": (
        (
            C(
                "p08_07_operator_actions_api",
                "a_dispatcher_takes_a_call_and_it_is_operator_entered",
            ),
            C("p08_07_operator_actions_api", "an_incident_commander_may_also_dispatch"),
        ),
        (
            C("p08_07_operator_actions_api", "only_dispatch_roles_may_take_a_call"),
            C("p08_07_operator_actions_api", "a_field_responder_cannot_move_a_unit"),
            C(
                "p08_07_ui_incident_dispatch",
                "an operator can read the dispatch screens but has no way to take a call",
            ),
        ),
    ),
    "routes.view": (
        (C("p07_02_routing", "api_serves_contract_shaped_route_alternatives"),),
        (C("p08_04_auth_api", "the_api_enforces_exactly_the_inventorys_capability"),),
    ),
    "recommendations.view": (
        (
            C(
                "p08_08_ui_actions",
                "recommendations show benefit, harm, confidence and the safety bounds",
            ),
        ),
        (
            C("p08_04_auth_api", "the_api_enforces_exactly_the_inventorys_capability"),
            C("p08_08_ui_actions", "an auditor can read but not act"),
        ),
    ),
    "commands.view": (
        (C("p08_08_ui_actions", "the list shows every lifecycle state as its own shape and word"),),
        (
            C("p08_04_ui_auth", "a field responder is refused the command screens, by capability"),
            C("p08_04_auth_api", "the_api_enforces_exactly_the_inventorys_capability"),
        ),
    ),
    "commands.request": (
        (
            C(
                "p08_08_command_workflow_api",
                "an_operator_requests_a_command_from_a_recommendation",
            ),
            C(
                "p08_08_command_workflow_api",
                "an_operator_requests_a_sign_message_directly_and_it_is_sc0",
            ),
            C(
                "p09_03_policy",
                "an_operator_may_request_an_sc_1_diversion_and_the_engine_names_the_role_it_used",
            ),
        ),
        (
            C(
                "p08_08_command_workflow_api",
                "roles_without_the_request_capability_are_refused_at_the_door",
            ),
            C(
                "p08_08_command_workflow_api",
                "roles_that_may_not_request_this_safety_class_are_refused",
            ),
            C("p09_03_policy", "every_other_role_is_refused_a_request_for_an_sc_1_action"),
        ),
    ),
    "commands.review": (
        (
            C(
                "p08_08_command_workflow_api",
                "a_supervisor_approves_after_the_policy_runs_again_against_what_is_true_now",
            ),
            C("p08_08_command_workflow_api", "a_different_operator_may_approve_an_sc0_command"),
        ),
        (
            C(
                "p08_08_command_workflow_api",
                "an_operator_may_not_approve_a_supervisor_class_command",
            ),
            C(
                "p08_08_command_workflow_api",
                "the_requester_cannot_approve_their_own_command_even_when_their_role_could_approve_that_class",
            ),
            C("p09_03_policy", "roles_without_the_review_capability_never_reach_a_decision"),
        ),
    ),
    "outcomes.view": (
        (C("p08_08_ui_actions", "outcomes name all four classes"),),
        (C("p08_04_auth_api", "the_api_enforces_exactly_the_inventorys_capability"),),
    ),
    "audit.view": (
        (
            C("p08_09_ui_govern", "lists what people and services did newest first"),
            C("p08_09_govern_api", "the_trail_unifies_operator_actions_and_state_histories"),
        ),
        (
            C("p08_09_govern_api", "only_the_auditor_role_can_read_the_audit_trail"),
            C("p08_09_ui_govern", "a role without audit.view is told which capability it lacks"),
        ),
    ),
    "ops.view": (
        (C("p08_09_ui_govern", "shows real probes: dependencies, worker heartbeats"),),
        (
            C(
                "p08_09_govern_api",
                "platform_status_is_for_operators_supervisors_commanders_and_auditors",
            ),
            C("p08_09_ui_govern", "a dispatcher is told which capability it lacks"),
        ),
    ),
    "handover.view": (
        (
            C("p08_09_ui_govern", "a supervisor writes one with open items"),
            C(
                "p08_09_govern_api",
                "every_operating_role_can_read_handovers_but_not_the_demo_operator",
            ),
        ),
        (C("p08_09_ui_govern", "the demo operator does not hold handover.view"),),
    ),
    "handover.write": (
        (
            C("p08_09_govern_api", "a_handover_names_its_author_from_the_token"),
            C("p08_09_govern_api", "the_incoming_person_acknowledges_by_name"),
        ),
        (
            C("p08_09_govern_api", "only_roles_with_handover_write_can_write_a_handover"),
            C(
                "p08_09_govern_api",
                "a_field_cannot_acknowledge_it_because_they_do_not_hold_handover_write",
            ),
        ),
    ),
    "demo.control": (
        (
            C(
                "p08_09_demo_controls",
                "the_demo_operator_starts_a_run_that_replays_the_real_p03_07_manifest",
            ),
            C("p08_09_ui_govern", "its own identity and banner"),
        ),
        (
            C("p08_09_demo_controls", "no_operating_role_can_start_a_demo_run"),
            C("p08_09_demo_controls", "an_operators_token_is_not_accepted_by_the_demo_controls"),
        ),
    ),
    "audit.export": (
        (
            C(
                "p09_05_audit_protection",
                "the_export_names_who_exported_it_when_and_under_which_filters",
            ),
            C("p09_05_audit_protection", "export_mode_additionally_drops_locations"),
        ),
        (
            C(
                "p09_05_audit_protection",
                "only_the_auditor_role_may_export_every_other_role_is_refused",
            ),
        ),
    ),
    "commands.override": (
        (
            C(
                "p09_05_audit_protection",
                "the_incident_commander_overrides_an_executed_sc2_command_with_a_justification",
            ),
        ),
        (
            C(
                "p09_05_audit_protection",
                "an_operator_holds_no_override_capability_and_is_refused_at_the_door",
            ),
            C(
                "p09_05_audit_protection",
                "a_supervisor_may_not_override_even_though_a_supervisor_may_approve_sc2",
            ),
        ),
    ),
}

# the six workflow classes P12.01's acceptance names, and what stands for each
WORKFLOW_CLASSES: tuple[R, ...] = (
    R(
        "W1",
        "Nominal: sign in, watch the live map and analytics, work incidents, hand over the shift - for every role that may",
        (
            C("p08_04_ui_auth", "lands on its own home screen", 7),
            C("p08_05_ui_map", "live operations map"),
            C("p08_09_ui_govern", "shift handover"),
            C("p08_10_ui_acceptance", "UX-01 accessibility, every screen, per role", 7),
        ),
        links=("RQ25", "UX-01"),
    ),
    R(
        "W2",
        "Peak: the ingestion path at ten times nominal, and the console under ten concurrent sessions",
        (
            C("p11_06_target_load", "peak_load_ten_times_nominal_loses_nothing"),
            C("p11_06_target_load", "a_30_minute_soak_at_nominal_load_loses_nothing"),
            C("p08_10_ui_acceptance", "LAT-05 and LOAD-04"),
        ),
        links=("LOAD-01", "LOAD-02", "LOAD-04"),
    ),
    R(
        "W3",
        "Safety: safety candidates become incidents that an operator owns, with pedestrian and cyclist conflicts labelled as inferred",
        (
            C("p06_05_safety", "every_candidate_cites_events_that_exist"),
            C(
                "p06_06_vru_conflicts",
                "api_serves_pedestrian_conflict_candidates_labelled_inferred_with_evidence",
            ),
            C(
                "p08_07_ui_incident_dispatch",
                "an operator moves the incident through its lifecycle",
            ),
        ),
        links=("RQ25",),
    ),
    R(
        "W4",
        "Emergency: a call is taken, a unit is assigned with a route, the call follows the unit to cleared",
        (
            C(
                "p08_07_operator_actions_api",
                "the_call_follows_its_unit_en_route_then_on_scene_then_cleared",
            ),
            C(
                "p08_07_ui_incident_dispatch",
                "assigning a unit finds route alternatives with ETA and uncertainty",
            ),
            C(
                "p07_10_scenarios",
                "the_recorded_assignment_timeline_equals_the_simulated_travel_and_carries_the_predicted_eta",
            ),
        ),
        links=("RQ25",),
    ),
    R(
        "W5",
        "Action: request, second-person approval, execution by the executor, independent outcome, override - each by the roles that may",
        (
            C("p08_08_command_workflow_api", "the_requester_cannot_approve_their_own_command"),
            C("p08_08_ui_actions", "approval is a confirmation that restates everything"),
            C("p07_09_outcomes", "api_outcomes_are_contract_valid"),
            C(
                "p09_05_audit_protection",
                "the_incident_commander_overrides_an_executed_sc2_command_with_a_justification",
            ),
        ),
        links=("RQ25", "SAFE-02"),
    ),
    R(
        "W6",
        "Security: every role's token is exactly its own role, every endpoint enforces the inventory, the policy engine fails closed",
        (
            C("p08_04_auth_api", "the_api_enforces_exactly_the_inventorys_capability"),
            C(
                "p11_04_target_security",
                "every_get_endpoint_lets_the_roles_the_inventory_allows_through",
            ),
            C(
                "p11_04_target_security",
                "with_the_policy_engine_stopped_a_permitted_request_is_refused_not_waved_through",
            ),
            C("p09_10_runtime_acceptance", "re_running_"),
        ),
        links=("RQ19", "RQ20"),
    ),
)

# ================================================================================================ P12.02 - the faults, and what the platform does
FAULTS: tuple[R, ...] = (
    # ---- edge
    R(
        "F-E1",
        "Edge: the process is killed mid-stream and starts again on its outbox",
        (
            C(
                "p12_02_rec01_edge_outage",
                "the_edge_process_was_killed_mid_stream_and_started_again",
            ),
            C(
                "p12_02_rec01_edge_outage",
                "every_durably_buffered_reading_reached_the_database_none_missing",
            ),
            C(
                "pytest:tests/test_edge_outbox.py",
                "test_committed_rows_survive_without_ever_calling_close",
            ),
            C(
                "pytest:tests/test_edge_outbox.py",
                "test_crash_before_ack_resends_but_receiver_rejects_the_duplicate",
            ),
        ),
        links=("REC-01",),
    ),
    R(
        "F-E2",
        "Edge: the model is missing, tampered or failing - the runtime says so and the baseline serves",
        (
            C(
                "pytest:tests/test_edge_runtime.py",
                "test_missing_model_falls_back_to_baseline_and_says_so",
            ),
            C(
                "pytest:tests/test_edge_runtime.py",
                "test_repeated_inference_errors_disable_the_model_and_fall_back",
            ),
            C(
                "pytest:tests/test_edge_runtime.py",
                "test_no_model_and_no_baseline_is_not_ready_and_abstains_explicitly",
            ),
            C("p10_04_alert_rules", "live_EdgeModelInactive_fires_on_its_induced_fault"),
        ),
        links=("RQ06", "RQ07"),
    ),
    R(
        "F-E3",
        "Edge: input is bad - invalid, duplicate, out of order, stale or unsynchronised",
        (
            C(
                "pytest:tests/test_edge_validation.py",
                "test_duplicate_event_id_and_sequence_conflict_and_out_of_order",
            ),
            C(
                "pytest:tests/test_edge_validation.py",
                "test_stale_event_is_accepted_with_flag_but_not_fresh",
            ),
            C("pytest:tests/test_edge_validation.py", "test_fuzzed_garbage_never_raises"),
        ),
        links=("RQ02",),
    ),
    # ---- network
    R(
        "F-N1",
        "Network: the uplink is lost (the broker stopped) and restored",
        (
            C("p12_02_rec01_edge_outage", "the_uplink_really_was_down"),
            C("p12_02_rec01_edge_outage", "readings_replay_in_order"),
            C("p11_06_target_load", "with_gateway_taken_away_for_25_s"),
        ),
        links=("REC-01",),
    ),
    R(
        "F-N2",
        "Network: the identity provider is unreachable",
        (
            C("p08_04_auth_api", "an_unreachable_identity_provider_fails_closed_503_never_open"),
            C(
                "p08_10_ui_acceptance",
                "an identity provider that cannot be reached when the token needs renewing",
            ),
            C("p08_10_ui_acceptance", "an identity provider that is down when a page is opened"),
        ),
        links=("RQ19",),
    ),
    R(
        "F-N3",
        "Network: a workload reaches what it should not (a broken isolation)",
        (
            C("p11_04_target_security", "network_api_cannot_reach_the_internet"),
            C("p11_04_target_security", "network_the_edge_runtime_cannot_reach_the_database"),
            C(
                "p11_04_target_security",
                "network_a_pod_of_another_namespace_cannot_reach_the_database",
            ),
        ),
        links=("RQ19", "RQ12"),
    ),
    # ---- stream
    R(
        "F-S1",
        "Stream: Kafka is unavailable while events keep arriving",
        (
            C("p05_03_gateway", "backpressure_no_premature_ack_and_gateway_alive"),
            C("p05_03_gateway", "backpressure_drains_after_recovery"),
            C("p11_06_target_load", "with_kafka_taken_away_for_25_s"),
        ),
        links=("RQ04",),
    ),
    R(
        "F-S2",
        "Stream: events are replayed or duplicated, and content conflicts",
        (
            C("p05_05_ingestion", "replay_never_duplicates_the_row"),
            C("p05_05_ingestion", "content_conflict_rejected"),
            C("p05_05_ingestion", "full_topic_replay_from_new_consumer_group_is_idempotent"),
        ),
        links=("REC-01",),
    ),
    R(
        "F-S3",
        "Stream: invalid events are refused at the gateway and never reach Kafka",
        (
            C("p05_03_gateway", "validation_rejects_invalid_event"),
            C("p05_03_gateway", "rejected_event_not_in_kafka"),
            C("p10_04_alert_rules", "live_IngestionRejectionRateHigh_fires_on_its_induced_fault"),
        ),
        links=("RQ04",),
    ),
    # ---- service
    R(
        "F-V1",
        "Service: each component is restarted (database, Kafka, broker, identity, gateway, API, ingestion, policy engine)",
        (
            C("p11_05_target_recovery", "after_a_restart_of_", 8),
            C("p11_05_target_recovery", "the_data_survived_every_restart"),
        ),
        links=("REC-02",),
    ),
    R(
        "F-V6",
        "Service: the command executor is killed while a command is executing - the orphan is reconciled before anything new runs and is never driven again",
        (
            C(
                "p12_02_rec02_executor_restart",
                "the_executor_process_was_killed_while_the_command_was_executing_and_left_it_executing",
            ),
            C(
                "p12_02_rec02_executor_restart",
                "on_start_the_new_executor_reconciles_the_orphan_it_is_failed_not_executed_and_not_retried",
            ),
            C(
                "p12_02_rec02_executor_restart",
                "the_orphan_was_reconciled_before_the_next_command_was_executed",
            ),
            C(
                "p12_02_rec02_executor_restart",
                "the_orphan_was_never_driven_again_the_adapter_was_not_called_for_it",
            ),
            C(
                "p12_02_rec02_executor_restart",
                "the_command_that_arrived_meanwhile_is_executed_exactly_once",
            ),
        ),
        links=("REC-02", "SAFE-02"),
    ),
    R(
        "F-V2",
        "Service: a worker is killed mid-action - the action is not repeated and the request is closed as abandoned",
        (
            C(
                "p10_09_recovery",
                "a_worker_killed_mid_action_leaves_a_request_that_is_closed_as_abandoned_not_left_running",
            ),
            C(
                "p10_09_recovery",
                "the_action_is_not_repeated_after_the_crash_and_the_incident_still_resolves",
            ),
        ),
        links=("REC-04",),
    ),
    R(
        "F-V3",
        "Service: a crashed edge runtime is detected, becomes one incident, is restarted once and resolves",
        (
            C(
                "p10_09_recovery",
                "every_crash_is_detected_becomes_one_incident_gets_one_restart_and_resolves",
            ),
            C(
                "p10_09_recovery",
                "the_runtime_really_came_back_from_a_new_process_the_container_start_time_changed",
            ),
            C("p10_09_recovery", "LAT_06_incident_to_remediation_start_p95_is_under_30_s"),
        ),
        links=("REC-04", "LAT-06"),
    ),
    R(
        "F-V4",
        "Service: a fault that cannot be repaired is escalated to a person and automation stands down",
        (
            C(
                "p10_09_recovery",
                "when_the_attempt_limits_are_reached_and_the_fault_persists_the_incident_is_escalated_to_a_person_with_the_reason_recorded",
            ),
            C(
                "p10_09_recovery",
                "after_the_escalation_automation_stands_down_no_further_action_is_taken",
            ),
        ),
        links=("REC-04",),
    ),
    R(
        "F-V5",
        "Service: a bad rollout of the API and of a worker",
        (
            C(
                "p11_05_target_recovery",
                "a_rollout_to_an_image_that_does_not_exist_leaves_the_old_api_serving_throughout",
            ),
            C(
                "p11_05_target_recovery",
                "the_same_fault_on_a_recreate_worker_is_an_outage_until_it_is_undone",
            ),
        ),
        links=("RQ22",),
    ),
    # ---- model
    R(
        "F-M1",
        "Model: a rotted active model is detected and rolled back to the previous verified version",
        (
            C(
                "p10_09_recovery",
                "a_rotted_active_model_raises_the_model_alerts_and_the_incident_is_recovered_by_rolling_back_the_registry",
            ),
            C(
                "p10_08_remediation",
                "the_model_registry_adapter_rolls_the_real_registry_format_back_one_verified_version_and_restarts_the_runtime",
            ),
        ),
        links=("RQ07", "REC-04"),
    ),
    R(
        "F-M2",
        "Model: a tampered package is refused at load and at activation",
        (
            C("p06_03_forecast_model", "tampered_or_unmanifested_package_is_refused"),
            C(
                "pytest:tests/test_edge_activation.py",
                "test_invalid_version_is_refused_and_pointer_is_untouched",
            ),
            C(
                "pytest:tests/test_edge_activation.py",
                "test_rollback_to_a_since_corrupted_version_refuses_and_keeps_current_serving",
            ),
        ),
        links=("RQ06",),
    ),
    R(
        "F-M3",
        "Model: input to a forecast is stale or has a gap - the service abstains",
        (
            C("p06_03_forecast_model", "stale_inputs_produce_no_forecast"),
            C(
                "p06_03_forecast_model",
                "a_gap_in_the_history_abstains_for_the_whole_network_context",
            ),
            C("p12_02_safe03_stale_state", "the_forecast_service_refuses_stale_input"),
        ),
        links=("SAFE-03",),
    ),
    # ---- policy
    R(
        "F-P1",
        "Policy: the engine is unreachable - every protected request is refused, an approved command is held, nothing is waved through",
        (
            C(
                "p09_03_policy",
                "with_the_engine_stopped_every_role_is_refused_with_503_policy_unavailable_never_served",
            ),
            C(
                "p09_03_policy",
                "during_the_outage_the_executor_holds_an_approved_command_it_does_not_execute_or_refuse_it",
            ),
            C(
                "p11_04_target_security",
                "with_the_policy_engine_stopped_a_permitted_request_is_refused_not_waved_through",
            ),
            C("p12_02_safe02_protected_actions", "policy_engine_unreachable_at_approval"),
            C("p12_02_safe02_protected_actions", "policy_engine_unreachable_at_execution"),
        ),
        links=("SAFE-02", "RQ19"),
    ),
    R(
        "F-P2",
        "Policy: the engine answers nothing, garbage or a loosely typed permit - treated as unavailable",
        (
            C(
                "p09_03_policy",
                "an_engine_that_answers_nothing_an_error_html_a_bare_string_or_a_loosely_typed_permit_is_treated_as_unavailable",
            ),
            C(
                "p09_03_policy",
                "an_empty_or_garbage_api_input_is_a_denial_never_a_permit_or_an_undefined_answer",
            ),
        ),
        links=("SAFE-02",),
    ),
    R(
        "F-P3",
        "Policy: what the engine enforces is what was reviewed (thousands of generated contexts against the reference)",
        (
            C(
                "p09_03_policy",
                "engine_and_reference_agree_on_thousands_of_generated_command_contexts_decision_code_and_message",
            ),
            C(
                "p09_03_policy",
                "the_generated_contexts_reach_every_kind_of_outcome_so_the_agreement_is_not_vacuous",
            ),
        ),
        links=("SAFE-02",),
    ),
    # ---- storage
    R(
        "F-D1",
        "Storage: the database volume is lost - restored from an encrypted backup with data, control and audit integrity",
        (
            C(
                "p11_05_target_recovery",
                "the_database_is_restored_from_the_encrypted_backup_roles_and_all",
            ),
            C(
                "p11_05_target_recovery",
                "every_row_written_before_the_backup_is_back_in_the_restored_database",
            ),
            C("p11_05_target_recovery", "the_audit_hash_chains_are_intact_after_the_restore"),
            C("p11_05_target_recovery", "a_backup_that_was_changed_by_one_bit_is_refused"),
        ),
        links=("REC-03",),
    ),
    R(
        "F-D2",
        "Storage: the database is unavailable during a load",
        (
            C("p11_06_target_load", "with_postgres_taken_away_for_25_s"),
            C(
                "p11_06_target_load",
                "no_event_the_broker_acknowledged_is_lost_in_any_of_the_four_degraded_modes",
            ),
        ),
        links=("LOAD-03",),
    ),
    R(
        "F-D3",
        "Storage: retention overdue and storage pressure are alerts",
        (
            C("p10_04_alert_rules", "live_RetentionOverdue_fires_on_its_induced_fault"),
            C(
                "p10_04_alert_rules",
                "live_RetentionUnderStoragePressure_fires_on_its_induced_fault",
            ),
            C("p05_10_retention", ".", 4),
        ),
        links=("RQ18",),
    ),
    # ---- certificates and configuration
    R(
        "F-C1",
        "Certificates: expiring and forged certificates are alerts and are refused",
        (
            C("p10_04_alert_rules", "live_CertificateExpiryCritical_fires_on_its_induced_fault"),
            C("p10_04_alert_rules", "live_DeviceCertificateExpiring_fires_on_its_induced_fault"),
            C("p11_04_target_security", "mqtt_a_certificate_from_another_ca_is_refused"),
        ),
        links=("RQ19",),
    ),
    R(
        "F-C2",
        "Configuration: a policy or data set that drifted from what was generated is caught",
        (
            C(
                "p09_03_policy",
                "the_policy_data_is_exactly_what_the_inventory_and_role_tables_generate",
            ),
            C(
                "p11_04_target_security",
                "the_policy_engine_holds_the_policy_version_this_release_was_generated_with",
            ),
        ),
        links=("RQ22",),
    ),
    # ---- the safety targets, measured by the labs of this phase
    R(
        "F-X1",
        "SAFE-02: a protected action that lacks evidence, a valid decision or fresh state is denied and never executed (every adapter, every way)",
        (
            C(
                "p12_02_safe02_protected_actions",
                "safe_02_every_attempt_that_lacked_evidence_a_valid_decision_or_fresh_state_was_stopped",
            ),
            C(
                "p12_02_safe02_protected_actions",
                "control_a_valid_command_is_approved_and_executed_exactly_once_on_every_adapter",
            ),
        ),
        links=("SAFE-02",),
    ),
    R(
        "F-X2",
        "SAFE-03: stale state fails safe in every consumer (state service, KPI record, forecast, routing, recommendation, command policy)",
        (
            C(
                "p12_02_safe03_stale_state",
                "routing_uses_a_fresh_measured_delay_and_ignores_the_same_window_once_it_is_old",
            ),
            C(
                "p12_02_safe03_stale_state",
                "a_signal_plan_recommendation_from_old_evidence_says_so",
            ),
            C("p12_02_safe03_stale_state", "policy_evidence_for_", 4),
        ),
        links=("SAFE-03",),
    ),
)
