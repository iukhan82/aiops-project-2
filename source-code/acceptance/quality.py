"""P12.04: safety, privacy, accessibility and fairness - the rows, the static scan for what the platform must not do, and the document (called by `verify_matrix.py p12_04`)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from acceptance import matrix as mx
from acceptance.matrix import Cite as C
from acceptance.matrix import Row as R
from backend.evidence import Evidence

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent

SAFETY = (
    R(
        "Q-S1",
        "Signals: no conflicting movements, no short yellow, no short pedestrian green or clearance - in every step of every platform run",
        (
            C(
                "p07_10_scenarios",
                "safe_01_zero_signal_safety_violations_over_every_step_of_every_platform_run",
            ),
            C(
                "p07_07_preemption",
                "signal_safety_monitor_reports_zero_violations_during_preemption",
            ),
            C(
                "pytest:tests/test_signal_safety.py",
                "test_two_conflicting_protected_greens_are_flagged",
            ),
            C("pytest:tests/test_signal_safety.py", "test_a_pedestrian_green_cut_short_is_flagged"),
        ),
        links=("SAFE-01",),
    ),
    R(
        "Q-S2",
        "Actions: a recommendation cannot invoke an adapter; a second person approves; only the executor executes; every step is audited",
        (
            C(
                "p08_08_command_workflow_api",
                "there_is_no_way_for_a_person_to_execute_a_command_through_the_api",
            ),
            C("p08_08_command_workflow_api", "the_requester_cannot_approve_their_own_command"),
            C(
                "p09_03_policy",
                "a_caller_that_is_not_the_executor_identity_is_refused_execution_even_for_a_perfect_command",
            ),
            C(
                "p08_08_command_workflow_api",
                "the_command_history_is_audited_request_refusals_and_approval_each_to_a_person",
            ),
        ),
        links=("SAFE-02",),
    ),
    R(
        "Q-S3",
        "Actions: an action that lacks evidence, a valid decision or fresh state is denied and never executed - every adapter, every way",
        (
            C(
                "p12_02_safe02_protected_actions",
                "safe_02_every_attempt_that_lacked_evidence_a_valid_decision_or_fresh_state_was_stopped",
            ),
            C("p12_02_safe02_protected_actions", "the_lab_can_fail_with_the_gate_switched_off"),
        ),
        links=("SAFE-02",),
    ),
    R(
        "Q-S4",
        "State: stale network and signal state fails safe in every consumer",
        (
            C(
                "p12_02_safe03_stale_state",
                "routing_uses_a_fresh_measured_delay_and_ignores_the_same_window_once_it_is_old",
            ),
            C("p12_02_safe03_stale_state", "policy_evidence_for_", 4),
            C("p08_10_ui_acceptance", "stale data is never left looking live"),
        ),
        links=("SAFE-03",),
    ),
    R(
        "Q-S5",
        "Emergency response: a missing route is reported not guessed, a policy outage and stale evidence leave the unit driving unassisted, staged response holds units back",
        (
            C(
                "p07_10_scenarios",
                "no_open_route_is_reported_not_guessed_and_nothing_is_pre_empted",
            ),
            C(
                "p07_10_scenarios",
                "policy_outage_fails_closed_the_command_never_executes_and_later_expires",
            ),
            C(
                "p07_10_scenarios",
                "stale_evidence_denies_the_preemption_and_the_unit_drives_on_unassisted",
            ),
            C(
                "p07_10_scenarios",
                "staged_response_holds_ems_back_until_fire_has_arrived_then_releases_it",
            ),
        ),
        links=("RQ25",),
    ),
    R(
        "Q-S6",
        "Outcomes: every executed action is verified independently, an unknown outcome is escalated, an unsafe one is rolled back with the undo said plainly",
        (
            C(
                "p07_09_outcomes",
                "a_post_action_telemetry_gap_is_unknown_and_escalated_never_defaulted_to_effective",
            ),
            C(
                "p07_09_outcomes",
                "an_unsafe_outcome_on_a_non_actuating_adapter_is_rolled_back_with_the_no_physical_undo_stated",
            ),
            C(
                "p07_09_outcomes",
                "an_undo_that_does_not_restore_leaves_the_command_executed_and_escalates",
            ),
        ),
        links=("RQ25",),
    ),
    R(
        "Q-S7",
        "A safe fallback plan when a signal controller fails",
        (),
        status="gap",
        limit="no governed action puts a failed controller on a safe plan and no rule detects the failure (scenario 5): the platform preserves safety by refusing to act, not by taking over.",
        owner="CONTROL/OPS - P12.05 (defect D-01)",
        links=("RQ25",),
    ),
    R(
        "Q-S8",
        "Edge: uncertain input is an explicit abstention with a reason, and the runtime never goes silent",
        (
            C(
                "pytest:tests/test_edge_runtime.py",
                "test_too_little_data_abstains_with_reason_not_a_guess",
            ),
            C(
                "pytest:tests/test_edge_runtime.py",
                "test_no_model_and_no_baseline_is_not_ready_and_abstains_explicitly",
            ),
            C("p06_05_safety", "edge_abstentions_never_raise_a_candidate"),
        ),
        links=("RQ06",),
    ),
)

PRIVACY = (
    R(
        "Q-P1",
        "Vision: privacy zones, ephemeral identities, a cohort floor, and no track point or object id ever leaves the edge",
        (
            C(
                "p06_06_vru_conflicts",
                "no_object_id_or_salt_appears_anywhere_in_the_exported_events",
            ),
            C("p06_06_vru_conflicts", "no_exported_aggregate_is_below_the_k_anonymity_floor"),
            C("p06_06_vru_conflicts", "privacy_zones_remove_samples_and_never_add_conflicts"),
            C(
                "pytest:tests/test_edge_vision_privacy.py",
                "test_a_single_pedestrian_is_never_individually_reported",
            ),
            C(
                "pytest:tests/test_edge_vision_privacy.py",
                "test_ephemeral_id_never_contains_the_raw_track_id",
            ),
        ),
        links=("RQ20",),
    ),
    R(
        "Q-P2",
        "Data: every category of data is inventoried with its privacy class and retention window, and the inventory is checked against the live schema",
        (
            C(
                "p09_06_data_inventory",
                "every_live_device_type_privacy_retention_combination_in_either_database_is_covered_by_a_category",
            ),
            C(
                "p09_06_data_inventory",
                "every_categorys_retention_class_is_a_real_retention_py_window",
            ),
            C("p05_10_retention", "audit_class_never_purged"),
            C("p05_10_retention", "short_class_event_purged_past_its_window"),
        ),
        links=("RQ20",),
    ),
    R(
        "Q-P3",
        "Audit and export: secrets and tokens are redacted before a row is written, exports drop locations and say who exported",
        (
            C(
                "p09_05_audit_protection",
                "workflow_audit_redacts_a_secret_before_the_row_is_written_not_only_when_asked_to",
            ),
            C("p09_05_audit_protection", "export_mode_additionally_drops_locations"),
            C(
                "p09_05_audit_protection",
                "the_log_redaction_filter_removes_a_token_from_a_log_records_rendered_message",
            ),
        ),
        links=("RQ20",),
    ),
    R(
        "Q-P4",
        "Emergency records carry no caller, crew or patient identifier",
        (
            C(
                "p09_06_data_inventory",
                "the_emergency_call_and_unit_assignment_contracts_really_carry_no_caller_or_crew_identifier_field",
            ),
            C("p07_03_cross_agency", "no_patient_or_medical_field_anywhere_in_the_case_file"),
        ),
        links=("RQ20",),
    ),
    R(
        "Q-P5",
        "Threat model: every trust boundary has threats scored separately for security and for safety, every implemented control has evidence that passes today",
        (
            C("p09_01_threat_model", "safety_impact_is_scored_separately_from_security_impact"),
            C(
                "p09_01_threat_model",
                "every_implemented_or_partial_control_has_existing_implementation_files_and_passing_named_evidence",
            ),
            C(
                "p09_01_threat_model",
                "every_trust_zone_in_the_security_diagram_has_a_boundary_and_threats",
            ),
        ),
        links=("RQ12", "RQ20"),
    ),
    R(
        "Q-P6",
        "Framework mapping: every applicable item names what closes it, and nothing claims compliance",
        (
            C(
                "p09_07_control_mapping",
                "no_item_text_claims_compliance_conformity_or_certification",
            ),
            C(
                "p09_07_control_mapping",
                "every_applicable_item_that_is_not_fully_evidenced_names_the_task_that_closes_it_or_why_it_is_accepted",
            ),
        ),
        links=("RQ20", "RQ23"),
    ),
)

ACCESSIBILITY = (
    R(
        "Q-A1",
        "WCAG 2.2 AA (axe), keyboard reach with a visible focus indicator and no keyboard trap on every screen each of the seven roles may open",
        (C("p08_10_ui_acceptance", "UX-01 accessibility, every screen, per role", 7),),
        links=("UX-01",),
    ),
    R(
        "Q-A2",
        "Reduced motion is honoured on every screen",
        (C("p08_10_ui_acceptance", "UX-01 reduced motion"),),
        links=("UX-01",),
    ),
    R(
        "Q-A3",
        "Colour is never the only signal: every state has a label and a shape, and every colour pair meets its contrast minimum",
        (
            C("p08_03_design", "every_declared_colour_pair_meets_its_wcag_2_2_minimum"),
            C(
                "p08_03_design",
                "no_two_states_of_a_lifecycle_share_a_shape_or_a_label_so_they_are_distinct_in_greyscale",
            ),
            C(
                "p08_03_design",
                "every_backend_state_has_a_label_shape_and_tone_and_no_invented_states",
            ),
        ),
        links=("UX-01",),
    ),
    R(
        "Q-A4",
        "Laptop, projector, phone, and 200% and 400% zoom: no sideways page scroll and no clipped or unreachable critical control",
        (
            C("p08_10_ui_acceptance", "UX-04 responsive and reflow", 3),
            C("p08_10_ui_acceptance", "UX-04 confirmation dialogs at 400% zoom"),
        ),
        links=("UX-04",),
    ),
    R(
        "Q-A5",
        "Every displayed value carries its truth label and freshness",
        (
            C(
                "p08_05_ui_map",
                "selecting a segment shows value, unit, observed time, truth label and freshness",
            ),
            C(
                "p08_06_ui_analytics",
                "shows the latest complete window with units, truth label and freshness",
            ),
            C("p08_10_ui_acceptance", "stale data is never left looking live"),
        ),
        links=("UX-02",),
    ),
    R(
        "Q-A6",
        "Critical actions need an explicit confirmation that defaults to cancel, and every state is shown distinctly",
        (
            C("p08_08_ui_actions", "approval is a confirmation that restates everything"),
            C("p08_07_ui_incident_dispatch", "resolving is a confirmation"),
            C(
                "p08_08_ui_actions",
                "the list shows every lifecycle state as its own shape and word",
            ),
        ),
        links=("UX-03",),
    ),
    R(
        "Q-A7",
        "Use with assistive technology by people who rely on it",
        (C("p08_10_ui_acceptance", "UX-01 accessibility, every screen, per role", 7),),
        status="partial",
        limit="axe, keyboard and reflow checks are automated; no screen-reader session and no user who relies on assistive technology took part, so what an automated check cannot see (reading order sense, announcements that mislead) is untested.",
        owner="UX/PRESENT - P12.05, with a real person in P14",
        links=("UX-01",),
    ),
)

FAIRNESS = (
    R(
        "Q-F1",
        "By place: the congestion detector finds episodes as well on one corridor and direction as on another (frozen parameters, held-out runs, non-selecting)",
        (
            C("p12_04_outcomes_by_group", "the_held_out_test_opening_is_ledgered_as_non_selecting"),
            C("p12_04_outcomes_by_group", "corridor_corridor-", 3),
            C("p12_04_outcomes_by_group", "the_disparity_between_corridors_is_stated"),
        ),
        links=("ACC-03",),
    ),
    R(
        "Q-F2",
        "By place: how many sensors watch each corridor is counted from the catalogue",
        (
            C(
                "p12_04_outcomes_by_group",
                "sensor_coverage_by_corridor_is_counted_from_the_device_catalogue",
            ),
        ),
        links=("RQ25",),
    ),
    R(
        "Q-F3",
        "By kind of emergency service: the ETA error of ambulance, fire and police is reported separately and each is inside its limit",
        (
            C(
                "p12_04_outcomes_by_group",
                "the_eta_error_of_each_emergency_service_is_reported_separately_never_pooled_away",
            ),
            C("p07_10_scenarios", "eta_01_every_scenario_within_15_percent_individually"),
            C("p07_10_scenarios", "eta_02_every_scenario_within_25_percent_individually"),
        ),
        links=("ETA-01", "ETA-02"),
    ),
    R(
        "Q-F4",
        "By road user: what pre-emption costs the traffic on the streets it crosses is measured, paired, with its worst case",
        (
            C(
                "p12_04_outcomes_by_group",
                "the_cost_of_pre_emption_to_the_streets_it_crosses_is_measured_paired_with_its_worst_case",
            ),
            C("p07_10_scenarios", "general_traffic_effect_was_measured_paired_and_is_reported"),
        ),
        links=("ETA-03",),
    ),
    R(
        "Q-F5",
        "By mode: cars, pedestrians, transit and emergency vehicles have measured outcomes",
        (
            C(
                "p07_10_scenarios",
                "safe_01_zero_signal_safety_violations_over_every_step_of_every_platform_run",
            ),
            C(
                "p07_08_transit_priority",
                "signal_safety_monitor_reports_zero_violations_in_the_baseline_and_priority_runs",
            ),
            C("p07_09_outcomes", "a_paired_transit_priority_run_is_effective_and_not_rolled_back"),
            C(
                "p06_06_vru_conflicts",
                "api_serves_pedestrian_conflict_candidates_labelled_inferred_with_evidence",
            ),
        ),
        links=("RQ25",),
    ),
    R(
        "Q-F6",
        "By mode: cyclists",
        (
            C(
                "p06_06_vru_conflicts",
                "cyclist_mode_is_reported_as_unvalidated_because_no_real_cyclist_conflict_exists",
            ),
        ),
        status="gap",
        limit="the simulator has no real cyclist conflict, so the cyclist detector is unvalidated and the API serves no cyclist candidate; nothing about outcomes for cyclists can be said.",
        owner="SIM/DATA - P12.05 (finding F-07)",
        links=("RQ25",),
    ),
    R(
        "Q-F7",
        "By people: whether outcomes differ across demographic or vulnerable groups",
        (),
        status="gap",
        limit="the district is synthetic and holds no population, demographic or vulnerability data, so no claim about people is possible and none is made; a real deployment would need that data, its consent and a design for measuring it.",
        owner="GRC - P12.05 (accepted limit of the simulation)",
        links=("RQ20",),
    ),
)

# What the platform must not do (docs/PROJECT_CONTEXT.md): facial recognition, plate reading, covert identity tracking, enforcement, patient data.
FORBIDDEN_IMPORTS = re.compile(
    r"^\s*(?:import|from)\s+(face_recognition|dlib|deepface|insightface|facenet|openalpr|easyocr|pytesseract|paddleocr|mtcnn)\b",
    re.MULTILINE | re.IGNORECASE,
)
FORBIDDEN_LOCK = re.compile(
    r"^(face[-_]recognition|dlib|deepface|insightface|openalpr|easyocr|pytesseract|paddleocr|mtcnn)\b",
    re.MULTILINE | re.IGNORECASE,
)


def static_scan() -> dict[str, list[str]]:
    hits = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if any(part in {"node_modules", ".venv", "__pycache__", "tests"} for part in path.parts):
            continue
        if FORBIDDEN_IMPORTS.search(path.read_text(encoding="utf-8", errors="replace")):
            hits.append(str(path.relative_to(SOURCE_ROOT)))
    locks = []
    for lock in SOURCE_ROOT.glob("*requirements*.txt"):
        locks += [
            f"{lock.name}: {m.group(0)}"
            for m in FORBIDDEN_LOCK.finditer(lock.read_text(encoding="utf-8"))
        ]
    for lock in (
        SOURCE_ROOT / "backend" / "requirements-lock.txt",
        SOURCE_ROOT / "edge" / "requirements-lock.txt",
    ):
        if lock.exists():
            locks += [
                f"{lock.name}: {m.group(0)}"
                for m in FORBIDDEN_LOCK.finditer(lock.read_text(encoding="utf-8"))
            ]
    no_enforcement = [
        str(p.relative_to(SOURCE_ROOT))
        for p in (SOURCE_ROOT / "backend").rglob("*.py")
        if re.search(
            r"speed[_ ]?camera|red[_ ]?light[_ ]?enforc|issue[_ ]?(?:a[_ ])?(?:fine|citation|ticket)",
            p.read_text(encoding="utf-8", errors="replace"),
            re.IGNORECASE,
        )
        and "verify_" not in p.name
    ]
    return {
        "face_or_plate_recognition_imports": hits,
        "recognition_libraries_in_the_locks": locks,
        "enforcement_features": no_enforcement,
    }


def dynamic_fairness(results: list[mx.Result]) -> list[mx.Result]:
    """Q-F1's verdict follows what the analysis found: covered when the corridors can be compared, partial (and saying why) when too few truth episodes exist to compare."""
    analysis = mx.load("p12_04_outcomes_by_group")
    if analysis is None:
        return results
    doc = json.loads((mx.EVIDENCE / "p12_04_outcomes_by_group.json").read_text(encoding="utf-8"))
    finding = doc["metrics"]["corridor_disparity"]
    rates = doc["metrics"]["congestion_detector_by_corridor_on_the_held_out_runs"]
    comparable = [n for n, r in rates.items() if r["enough_truth_to_compare"]]
    out = []
    for r in results:
        if (
            r.row.id == "Q-F1"
            and r.verdict == "covered"
            and (len(comparable) < len(rates) or doc["metrics"].get("intervals_overlap") is False)
        ):
            row = R(
                r.row.id,
                r.row.title,
                r.row.cites,
                status="partial",
                limit=f"{finding}. Corridors compared: {', '.join(comparable) or 'none'} of {', '.join(rates)}; the held-out runs hold few truth episodes, so the intervals are wide and a difference (or its absence) is not established.",
                owner="DATA - P12.05 (finding F-04)",
                links=r.row.links,
            )
            r2 = mx.evaluate(row)
            out.append(r2)
        else:
            out.append(r)
    return out


def run() -> int:
    ev = Evidence("P12.04", "p12_04_quality_matrix", docs_name="p12_04_quality_matrix")
    groups = {
        "Safety": SAFETY,
        "Privacy": PRIVACY,
        "Accessibility": ACCESSIBILITY,
        "Fairness by place, kind of road user and mode": FAIRNESS,
    }
    evaluated = {name: mx.evaluate_all(rows) for name, rows in groups.items()}
    evaluated["Fairness by place, kind of road user and mode"] = dynamic_fairness(
        evaluated["Fairness by place, kind of road user and mode"]
    )
    everything = [r for rs in evaluated.values() for r in rs]
    failed = [(r.row.id, r.problems) for r in everything if r.verdict == "FAILED"]
    ev.check(
        "every_quality_row_cites_evidence_that_exists_and_every_cited_check_passes",
        not failed,
        f"{len(everything)} rows; failed {failed[:3]}",
    )
    unowned = [
        r.row.id
        for r in everything
        if r.row.status in ("partial", "gap") and not (r.row.limit and r.row.owner)
    ]
    ev.check(
        "every_partial_or_gap_quality_row_says_what_is_not_shown_and_who_owns_it",
        not unowned,
        f"unowned {unowned}",
    )
    scan = static_scan()
    ev.check(
        "no_source_file_imports_a_face_or_plate_recognition_library",
        not scan["face_or_plate_recognition_imports"],
        str(scan["face_or_plate_recognition_imports"]),
    )
    ev.check(
        "no_dependency_lock_carries_a_face_or_plate_recognition_library",
        not scan["recognition_libraries_in_the_locks"],
        str(scan["recognition_libraries_in_the_locks"]),
    )
    ev.check(
        "no_backend_source_file_implements_automated_enforcement",
        not scan["enforcement_features"],
        str(scan["enforcement_features"]),
    )
    record = None
    runs = SOURCE_ROOT / "infra" / "platform" / "output" / "acceptance_runs.json"
    if runs.exists():
        record = next(
            (
                r
                for r in json.loads(runs.read_text(encoding="utf-8"))["runs"]
                if r["id"] == "unit-tests"
            ),
            None,
        )
    ev.check(
        "the_python_unit_tests_the_matrix_cites_passed_in_the_run_of_this_phase",
        bool(record and record["passed"]),
        record["started"] if record else "no run record",
    )
    counts = mx.tally(everything)
    verdict = (
        "PASSED"
        if counts["partial"] == 0 and counts["gap"] == 0 and counts["FAILED"] == 0
        else "NOT PASSED"
    )
    gate = f"{verdict}: {counts['covered']} covered, {counts['partial']} partial, {counts['gap']} gap, {counts['FAILED']} failed"
    ev.check("the_gate_result_is_stated_in_numbers", True, gate)
    ev.metrics = {
        "gate": gate,
        "counts": counts,
        "by_group": {n: mx.tally(rs) for n, rs in evaluated.items()},
        "rows": {
            r.row.id: {
                "verdict": r.verdict,
                "title": r.row.title,
                "not_shown": r.row.limit,
                "owner": r.row.owner,
            }
            for r in everything
        },
        "static_scan": scan,
    }
    ev.notes["gate"] = gate
    text = f"# P12.04 safety, privacy, accessibility and fairness\n\nGenerated by `source-code/acceptance/verify_matrix.py p12_04` from the evidence in `docs/evidence/`; do not edit by hand.\n\n**Gate: {gate}.**\n\n"
    for name, rs in evaluated.items():
        text += mx.render(name, rs) + "\n"
    text += (
        "## What the platform must not do (static scan)\n\n"
        + "\n".join(
            f"- {k.replace('_', ' ')}: {'none found' if not v else v}" for k, v in scan.items()
        )
        + "\n"
    )
    (REPO_ROOT / "docs" / "evidence" / "P12_04_QUALITY_MATRIX.md").write_text(
        text, encoding="utf-8", newline="\n"
    )
    return ev.finish()
