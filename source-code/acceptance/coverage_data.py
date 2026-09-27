"""P12.07: the thirty assessment requirements (RQ01-RQ30) and what stands behind each, stated as the traceability document's own vocabulary.

Status words (`docs/requirements/TRACEABILITY.md`): TESTED - automated evidence passes for the requirement as normalized, with its limits recorded; IMPLEMENTED - built, and evidenced with
material gaps or only in part; PLANNED - belongs to Phase 13 or 14 (documents, the repository release, the presentation, and everything that needs the student); GAP - the requirement is not met.
DEMONSTRATED is never used here: it needs an assessor in the room, and no agent can record that.

Every TESTED and IMPLEMENTED row cites evidence that must pass today; a PLANNED row names the task that owns it; a GAP row says what is missing and who decides. Nothing here is an assessor's approval.
"""

from __future__ import annotations

from dataclasses import dataclass

from acceptance.matrix import Cite as C


@dataclass(frozen=True)
class Requirement:
    id: str
    status: str  # TESTED | IMPLEMENTED | PLANNED | GAP
    cites: tuple[C, ...] = ()
    note: str = ""  # what limits the status, or what is planned and by which task
    tasks: tuple[
        str, ...
    ] = ()  # register tasks that own the rest (PLANNED and GAP, and the unfinished part of IMPLEMENTED)


REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement(
        "RQ01",
        "IMPLEMENTED",
        (
            C("p12_01_acceptance_matrix", "the_gate_result_is_stated_in_numbers"),
            C("p12_02_fault_matrix", "every_fault_row_cites_evidence"),
            C("p11_08_runbook_drill_deploy", "runbook_step_up"),
        ),
        "the platform runs end to end on a real host and the scenario steps stand on passing evidence, except those the P12.01 matrix lists as partial or a gap: the signal-failure scenario is not implemented (D-01)",
        ("P12.05",),
    ),
    Requirement(
        "RQ02",
        "TESTED",
        (
            C(
                "pytest:tests/test_edge_outbox.py",
                "test_uplink_outage_then_recovery_preserves_all_events_and_order",
            ),
            C(
                "p12_02_rec01_edge_outage",
                "every_durably_buffered_reading_reached_the_database_none_missing",
            ),
            C(
                "pytest:tests/test_edge_runtime.py",
                "test_startup_with_verified_model_is_ready_and_not_degraded",
            ),
        ),
        "one simulated edge site; the edge decides locally and buffers through an outage",
    ),
    Requirement(
        "RQ03",
        "GAP",
        (
            C("p11_03_target_deployment", "every_workload_the_manifests_declare"),
            C("doc:docs/decisions/ADR-0007-central-cloud-placement.md", "."),
        ),
        "the central tier runs on the workstation and, deployed from the same manifests, on one real Ubuntu host; nothing runs on a remote cloud. Whether that satisfies the assessment is the assessor's decision (F-17).",
        ("P13.02",),
    ),
    Requirement(
        "RQ04",
        "TESTED",
        (
            C("p11_06_target_load", "peak_load_ten_times_nominal_loses_nothing"),
            C("p11_06_target_load", "a_30_minute_soak_at_nominal_load_loses_nothing"),
            C(
                "p11_04_target_security",
                "mqtt_a_device_with_a_certificate_from_the_platform_ca_connects",
            ),
        ),
        "measured on one node of a shared host with synthetic events: clean to 312 events/s, a 30-minute soak, 56 native security checks",
    ),
    Requirement(
        "RQ05",
        "TESTED",
        (
            C("p11_03_target_deployment", "every_workload_the_manifests_declare"),
            C("p11_04_target_security", "network_the_edge_runtime_cannot_reach_the_internet"),
            C(
                "p11_03_target_deployment",
                "the_platform_images_running_in_the_cluster_are_the_ones_the_workstation_signed",
            ),
        ),
        "the edge runtime is a non-root, read-only, capability-free container with resource limits, deployed in a Kubernetes cluster",
    ),
    Requirement(
        "RQ06",
        "TESTED",
        (
            C(
                "pytest:tests/test_edge_model_runtime.py",
                "test_golden_vectors_reproduce_probabilities_and_decisions",
            ),
            C(
                "pytest:tests/test_edge_runtime.py",
                "test_model_inference_latency_is_far_below_the_100ms_budget",
            ),
            C("doc:docs/evidence/edge_benchmark_report.json", "."),
        ),
        "ONNX Runtime on CPU with one thread under 0.75 CPU and 512 MiB: warm inference p95 0.135 ms against a 100 ms target (LAT-01); the model beats the baseline on F1 with wide intervals (ACC-01)",
    ),
    Requirement(
        "RQ07",
        "TESTED",
        (
            C("p10_04_alert_rules", "live_EdgeModelInactive_fires_on_its_induced_fault"),
            C(
                "p10_09_recovery",
                "every_crash_is_detected_becomes_one_incident_gets_one_restart_and_resolves",
            ),
            C(
                "p10_08_remediation",
                "the_model_registry_adapter_rolls_the_real_registry_format_back_one_verified_version_and_restarts_the_runtime",
            ),
        ),
        "alerts, correlated incidents and bounded remediation with independent recovery verification; false incidents 29.6% against a 10% target (FA-03, F-08) and scale, failover and quarantine are plan-only (F-14)",
    ),
    Requirement(
        "RQ08",
        "IMPLEMENTED",
        (
            C("doc:diagrams/sources/02-network-flow.mmd", "."),
            C("doc:diagrams/exports/02-network-flow.svg", "."),
        ),
        "an editable source and an export exist from Phase 02; they are updated to the tested implementation and inspected in P13.01",
        ("P13.01",),
    ),
    Requirement(
        "RQ09",
        "IMPLEMENTED",
        (
            C("doc:diagrams/sources/03-data-flow.mmd", "."),
            C("doc:diagrams/exports/03-data-flow.svg", "."),
        ),
        "as RQ08",
        ("P13.01",),
    ),
    Requirement(
        "RQ10",
        "IMPLEMENTED",
        (
            C("doc:diagrams/sources/01-system-architecture.mmd", "."),
            C("doc:diagrams/sources/07-deployment-topology.mmd", "."),
        ),
        "as RQ08; the proposed production views are separate and labelled not deployed (P13.02)",
        ("P13.01", "P13.02"),
    ),
    Requirement(
        "RQ11",
        "IMPLEMENTED",
        (
            C("doc:diagrams/sources/04-workflow-command-safety-path.mmd", "."),
            C("p12_01_acceptance_matrix", "every_scenario_step_row_cites_evidence"),
        ),
        "as RQ08",
        ("P13.01",),
    ),
    Requirement(
        "RQ12",
        "IMPLEMENTED",
        (
            C("doc:docs/security/SECURITY_ARCHITECTURE.md", "."),
            C("doc:diagrams/sources/05-security-trust-boundaries.mmd", "."),
            C(
                "p09_01_threat_model",
                "every_trust_zone_in_the_security_diagram_has_a_boundary_and_threats",
            ),
        ),
        "the threat model covers every trust boundary; the diagram is updated in P13.01",
        ("P13.01",),
    ),
    Requirement(
        "RQ13",
        "IMPLEMENTED",
        (
            C("doc:docs/decisions/ADR-0007-central-cloud-placement.md", "."),
            C("doc:diagrams/sources/06-hybrid-cloud-placement.mmd", "."),
        ),
        "the placement decision is recorded with the deployed/proposed distinction; the proposed view is finished in P13.02",
        ("P13.02",),
    ),
    Requirement(
        "RQ14",
        "PLANNED",
        (),
        "the release, the publication review and the authorized push are P13.05-P13.07; the push is the user's",
        ("P13.05", "P13.06", "P13.07"),
    ),
    Requirement(
        "RQ15",
        "IMPLEMENTED",
        (
            C(
                "p11_03_target_deployment",
                "the_cluster_runs_the_kubernetes_minor_version_the_manifests_are_validated_against",
            ),
            C("p11_08_runbook_drill_deploy", "runbook_step_cluster"),
            C("doc:docs/environment/CONTAINER_K3S_FALCO_FEASIBILITY.md", "."),
        ),
        "the platform runs in a kind cluster (Kubernetes v1.36.4, the minor of the host's K3s) with Docker, deployed by a runbook that was followed as written; it is not deployed on K3s itself, which was proven feasible in P01.07",
    ),
    Requirement(
        "RQ16",
        "TESTED",
        (
            C(
                "pytest:tests/test_edge_model_runtime.py",
                "test_released_package_loads_and_matches_runtime_feature_contract",
            ),
            C(
                "pytest:tests/test_edge_vision_privacy.py",
                "test_a_single_pedestrian_is_never_individually_reported",
            ),
        ),
        "Python with ONNX Runtime; the vision path is privacy-preserving feature aggregation, not OpenCV inference on video",
    ),
    Requirement(
        "RQ17",
        "TESTED",
        (
            C("p05_03_gateway", "backpressure_drains_after_recovery"),
            C("p05_05_ingestion", "replay_never_duplicates_the_row"),
            C("p11_06_target_load", "with_kafka_taken_away_for_25_s"),
        ),
        "Mosquitto over mutual TLS at the edge and Redpanda (Kafka API) centrally",
    ),
    Requirement(
        "RQ18",
        "TESTED",
        (
            C("p10_03_observability_stack", "."),
            C("p10_04_alert_rules", "healthy_baseline_no_alert_fires"),
            C(
                "p11_03_target_deployment",
                "prometheus_scrapes_the_edge_runtime_and_tempo_and_both_are_up",
            ),
            C("p10_01_observability", "."),
        ),
        "Prometheus, Grafana, Tempo, Loki and OpenTelemetry on the workstation stack and on the target",
    ),
    Requirement(
        "RQ19",
        "TESTED",
        (
            C("p09_02_keycloak", "."),
            C(
                "p09_03_policy",
                "with_the_engine_stopped_every_role_is_refused_with_503_policy_unavailable_never_served",
            ),
            C("p11_01_service_images", "backend_no_fixable_critical_or_high_vulnerability"),
            C("p09_09_falco_target", "every_platform_alert_names_the_pod"),
        ),
        "Keycloak (Authorization Code with PKCE), OPA, Trivy (scan of the shipped images) and Falco (on the target); service-to-service TLS and alert routing are open (F-12, F-13)",
    ),
    Requirement(
        "RQ20",
        "IMPLEMENTED",
        (
            C(
                "p09_07_control_mapping",
                "no_item_text_claims_compliance_conformity_or_certification",
            ),
            C(
                "p09_06_data_inventory",
                "every_live_device_type_privacy_retention_combination_in_either_database_is_covered_by_a_category",
            ),
            C("p09_01_threat_model", "residual_risk_is_computed_from_control_state"),
        ),
        "a scoped mapping to GDPR, ISO 27001, IEC 62443, NIST CSF, ETSI EN 303 645, OWASP ASVS and AI frameworks with an explicit gap list; no compliance claim; eleven controls are partial (F-10)",
        ("P12.05", "P13.04"),
    ),
    Requirement(
        "RQ21",
        "PLANNED",
        (C("doc:docs/decisions/ADR-0002-streaming-transport.md", "."),),
        "eight decision records exist; explaining and defending them is the student's, in P14",
        ("P14.01", "P14.05", "P14.08"),
    ),
    Requirement(
        "RQ22",
        "TESTED",
        (
            C("p11_08_runbook_drill_reconcile_recover", "runbook_step_verify-recovered"),
            C("p12_02_fault_matrix", "the_gate_result_is_stated_in_numbers"),
            C(
                "p11_05_target_recovery",
                "the_database_is_restored_from_the_encrypted_backup_roles_and_all",
            ),
            C("p11_06_target_load", "a_ceiling_was_found"),
        ),
        "configuration and deployment (a runbook followed as written), operation, failure and recovery (27 fault rows), scale (a measured ceiling) and security (56 native checks)",
    ),
    Requirement(
        "RQ23",
        "IMPLEMENTED",
        (
            C("doc:docs/security/RISK_REGISTER.md", "."),
            C("doc:docs/security/DATA_INVENTORY_AND_DPIA.md", "."),
            C(
                "p09_07_control_mapping",
                "every_applicable_item_that_is_not_fully_evidenced_names_the_task_that_closes_it_or_why_it_is_accepted",
            ),
        ),
        "the policies, roles, controls, risk register and DPIA-style review are written and checked against the code; the operational, security and privacy runbooks are exercised in P13.04",
        ("P13.04",),
    ),
    Requirement(
        "RQ24",
        "PLANNED",
        (),
        "the deck, its notes, PDF and rehearsal are P14.01-P14.03 and P14.09",
        ("P14.01", "P14.02", "P14.03", "P14.09"),
    ),
    Requirement(
        "RQ25",
        "IMPLEMENTED",
        (
            C("p12_01_acceptance_matrix", "every_scenario_step_row_cites_evidence"),
            C(
                "p07_10_scenarios",
                "eta_03_pre_emption_reduces_travel_time_on_average_and_never_worsens_a_pair_beyond_the_outcome_tolerance",
            ),
            C(
                "p10_09_recovery",
                "a_rotted_active_model_raises_the_model_alerts_and_the_incident_is_recovered_by_rolling_back_the_registry",
            ),
        ),
        "real-time processing, edge AI and the response to an event are evidenced for scenarios 1, 2, 3, 6 and 7 and in part for 4; scenario 5 is a gap (D-01); the live demonstration and its offline backup are P14.04",
        ("P12.05", "P14.04"),
    ),
    Requirement(
        "RQ26",
        "PLANNED",
        (),
        "the question bank and the mock interview are P14.05 and P14.09; the interview needs the student",
        ("P14.05", "P14.09"),
    ),
    Requirement(
        "RQ27",
        "PLANNED",
        (),
        "the M2, M3 and M4 walkthroughs and the live redesign need the student and cannot be done or inferred by an agent",
        ("P14.06", "P14.07", "P14.08", "P14.09"),
    ),
    Requirement(
        "RQ28",
        "PLANNED",
        (),
        "an AI-use statement and log for the student to own and defend is P14; AGENTS.md records that agents assist and labels every simulated or inferred value",
        ("P14.02", "P14.09"),
    ),
    Requirement(
        "RQ29",
        "PLANNED",
        (),
        "the rubric review and the rendered-deck inspection are P14.03",
        ("P14.03",),
    ),
    Requirement(
        "RQ30",
        "PLANNED",
        (),
        "at least ten structured questions are written in the P14.05 bank and rehearsed in P14.09",
        ("P14.05", "P14.09"),
    ),
)
