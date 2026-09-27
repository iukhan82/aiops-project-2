"""P10.07: the platform-incident correlator's grouping rules on hand-built (synthetic) signal sets.

These pin the rules down; they say nothing about how the rules perform on the platform's real alerts - that is measured
by `backend/aiops/verify_platform_incidents.py` against the recorded P10.04 alert timeline.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from backend.aiops import correlation as c

T0 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
RULES = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "platform"
    / "observability"
    / "prometheus"
    / "rules"
    / "aiops-alerts.yml"
)


def alert(name, onset_s=0, last_s=300, severity="critical", active=True, **labels):
    return c.alert_signal(
        name,
        labels,
        severity,
        onset=T0 + timedelta(seconds=onset_s),
        first_seen=T0 + timedelta(seconds=onset_s),
        last_seen=T0 + timedelta(seconds=last_s),
        occurrences=1,
        active=active,
    )


def anomaly(signal, onset_s=0, last_s=300, active=True):
    return c.anomaly_signal(
        signal,
        deviation=9.0,
        onset=T0 + timedelta(seconds=onset_s),
        first_seen=T0 + timedelta(seconds=onset_s),
        last_seen=T0 + timedelta(seconds=last_s),
        occurrences=1,
        active=active,
    )


NOW = T0 + timedelta(seconds=300)


def keys(drafts):
    return {d.key for d in drafts}


def test_a_database_outage_and_the_symptoms_it_explains_are_one_incident():
    drafts = c.correlate(
        [
            alert("PlatformTargetDown", 0, service_name="postgres"),
            alert("ApiErrorRateHigh", 40, severity="warning"),
            alert("ApiLatencyP95High", 60, severity="warning"),
            alert("IngestionLatencyP95High", 50, severity="warning"),
        ],
        NOW,
    )
    assert keys(drafts) == {"postgres"}
    (draft,) = drafts
    assert draft.components == ["postgres", "api", "ingestion"]
    assert len(draft.signals) == 4
    assert {d["explained_by"] for d in draft.hypothesis["downstream"]} == {
        "PlatformTargetDown{service_name=postgres}"
    }
    assert draft.severity == "critical"  # a component that cannot be reached


def test_a_symptom_that_began_long_before_the_supposed_cause_is_not_explained_by_it():
    drafts = c.correlate(
        [
            alert("ApiErrorRateHigh", 0, severity="warning"),
            alert("PlatformTargetDown", 300, last_s=400, service_name="postgres"),
        ],
        T0 + timedelta(seconds=400),
    )
    assert keys(drafts) == {"api", "postgres"}


def test_a_cause_noticed_a_little_after_its_first_symptom_still_explains_it():
    drafts = c.correlate(
        [
            alert("ApiErrorRateHigh", 0, severity="warning"),
            alert("PlatformTargetDown", c.ONSET_SKEW_S - 10, service_name="postgres"),
        ],
        NOW,
    )
    assert keys(drafts) == {"postgres"}


def test_a_downstream_failure_never_explains_an_upstream_symptom():
    drafts = c.correlate(
        [
            alert("PlatformTargetDown", 0, service_name="api"),
            alert("IngestionLatencyP95High", 30, severity="warning"),
        ],
        NOW,
    )
    assert keys(drafts) == {"api", "ingestion"}


def test_signals_nothing_links_stay_separate_incidents():
    drafts = c.correlate(
        [
            alert("CertificateExpiryCritical", 0, service_name="postgres"),
            alert("DevicesSilent", 5, severity="warning"),
            alert("PolicyVersionDrift", 8),
        ],
        NOW,
    )
    assert keys(drafts) == {"postgres", "devices", "opa"}


def test_advisory_signals_do_not_absorb_operational_ones():
    drafts = c.correlate(
        [
            alert("DatabaseSizeCritical", 0),
            alert("CertificateExpiryCritical", 0, service_name="postgres"),
            alert("ApiErrorRateHigh", 20, severity="warning"),
        ],
        NOW,
    )
    assert keys(drafts) == {"postgres", "api"}


def test_an_alert_and_the_detector_on_the_same_component_are_one_duplicate_symptom():
    (draft,) = c.correlate(
        [
            alert("IngestionLatencyP95High", 0, severity="warning"),
            anomaly("ingest_latency_p95_ms", 5),
        ],
        NOW,
    )
    assert draft.key == "ingestion"
    assert draft.hypothesis["corroborated_by_sources"] == ["alert", "anomaly"]
    assert len(draft.signals) == 2


def test_the_same_signal_listed_twice_is_one_signal():
    a = alert("EdgeModelInactive", 0, severity="warning")
    b = alert("EdgeModelInactive", 30, severity="warning")
    (draft,) = c.correlate([a, b], NOW)
    assert len(draft.signals) == 1


def test_a_failure_chain_collapses_into_the_incident_of_its_earliest_root():
    (draft,) = c.correlate(
        [
            alert("PlatformTargetDown", 0, service_name="mqtt-broker"),
            alert("GatewayDeliveryStalled", 30),
            alert("NetworkStateFreshnessLow", 60, severity="warning"),
        ],
        NOW,
    )
    assert draft.key == "mosquitto"
    assert draft.components == ["mosquitto", "gateway", "ingestion"]
    by_signal = {d["signal"]: d for d in draft.hypothesis["downstream"]}
    assert by_signal["NetworkStateFreshnessLow"]["explained_by"] == (
        "PlatformTargetDown{service_name=mqtt-broker}"
    )
    assert by_signal["NetworkStateFreshnessLow"][
        "because"
    ]  # the reasons are recorded, not just the verdict


def test_every_hypothesis_is_labelled_unverified_and_names_only_known_components():
    drafts = c.correlate(
        [
            alert("PlatformTargetDown", 0, service_name="postgres"),
            alert("ApiErrorRateHigh", 40, severity="warning"),
            alert("EdgeRuntimeDown", 5),
            anomaly("unattributed", 10),
        ],
        NOW,
    )
    assert drafts
    for d in drafts:
        assert d.hypothesis["verified"] is False
        assert set(d.components) <= c.COMPONENTS
        assert "inference" in d.hypothesis["note"].lower()


def test_an_alert_the_model_does_not_know_is_kept_not_dropped_and_the_watchdog_is_ignored():
    unknown = alert("SomethingNew", 0, severity="warning")
    assert unknown is not None and unknown.component == "platform"
    assert alert("AlertPipelineWatchdog", 0, severity="none") is None


def test_a_cleared_incident_reports_how_long_it_has_been_quiet():
    (draft,) = c.correlate(
        [alert("ApiLatencyP95High", 0, last_s=100, active=False, severity="warning")], NOW
    )
    assert not draft.active
    assert draft.quiet_for_s == 200.0


def test_every_alert_rule_is_modelled_and_every_model_entry_is_a_real_rule():
    doc = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    rules = {r["alert"] for g in doc["groups"] for r in g["rules"]} - {c.WATCHDOG}
    assert rules == set(c.ALERT_MODEL)


def test_every_alert_model_names_a_real_component_and_a_known_effect():
    for name, model in c.ALERT_MODEL.items():
        assert model.effect in c.EFFECTS, name
        assert model.component in c.COMPONENTS or model.component == "service_name", name
        assert model.caused_by <= {"unavailable", "data_loss"}, name
    assert set(c.SERVICE_COMPONENT.values()) <= c.COMPONENTS
    assert {comp for comp, _ in c.ANOMALY_MODEL.values()} <= c.COMPONENTS


def test_the_data_flow_and_dependency_graph_only_name_real_components():
    for a, b in c.DATA_FLOW:
        assert a in c.COMPONENTS and b in c.COMPONENTS
    for client, server in c.DEPENDS_ON:
        assert client in c.COMPONENTS and server in c.COMPONENTS
