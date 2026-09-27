"""P10.04: generate the promtool unit-test file for `rules/aiops-alerts.yml`.

    python source-code/infra/platform/observability/prometheus/build_rule_tests.py          # writes rule-tests/
    python source-code/infra/platform/observability/prometheus/build_rule_tests.py --check  # fails if the file is stale

Every alert has at least one case that must fire and one healthy case that must not; the expected alert carries the rule's
own static labels plus the labels the expression preserves, and its annotations are rendered from the rule file, so the
tests cannot drift from the wording. `backend/aiops/verify_alerts.py` runs `promtool test rules` on the result.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
RULES = HERE / "rules" / "aiops-alerts.yml"
OUT = HERE / "rule-tests" / "aiops-alerts.test.yml"

EDGE = 'job="edge-runtime",instance="aiops-edge-runtime:9100"'
EDGE_LABELS = {"job": "edge-runtime", "instance": "aiops-edge-runtime:9100"}


@dataclass
class Case:
    alert: str
    title: str
    series: dict[str, str]
    eval_time: str
    fires: bool
    extra: dict[str, str] = field(default_factory=dict)


def histogram(name: str, per_le: dict[str, str], extra: str = "") -> dict[str, str]:
    return {f'{name}_bucket{{le="{le}"{extra}}}': values for le, values in per_le.items()}


CASES: list[Case] = [
    # ---- pipeline
    Case("AlertPipelineWatchdog", "always firing", {'up{job="x"}': "1x4"}, "1m", True),
    Case(
        "PlatformProbeStale",
        "probe timestamp stops advancing",
        {"platform_probe_success_timestamp_seconds": "0x40"},
        "4m",
        True,
    ),
    Case(
        "PlatformProbeStale",
        "probe timestamp tracks time",
        {"platform_probe_success_timestamp_seconds": "0+15x40"},
        "4m",
        False,
    ),
    Case("PlatformProbeMissing", "no probe series", {'up{job="x"}': "1x40"}, "6m", True),
    Case(
        "PlatformProbeMissing",
        "probe series present",
        {"platform_probe_success_timestamp_seconds": "0+15x40"},
        "6m",
        False,
    ),
    Case(
        "PlatformTargetDown",
        "postgres unreachable",
        {'platform_probe_target_up{service_name="postgres"}': "0x20"},
        "3m",
        True,
        {"service_name": "postgres"},
    ),
    Case(
        "PlatformTargetDown",
        "postgres reachable",
        {'platform_probe_target_up{service_name="postgres"}': "1x20"},
        "3m",
        False,
    ),
    # ---- device
    Case(
        "DevicesSilent",
        "30% stale",
        {
            'platform_devices_active{device_type="traffic_loop"}': "100x40",
            'platform_devices_stale{device_type="traffic_loop"}': "30x40",
            'platform_devices_never_reported{device_type="traffic_loop"}': "0x40",
        },
        "4m",
        True,
        {"device_type": "traffic_loop"},
    ),
    Case(
        "DevicesSilent",
        "5% stale",
        {
            'platform_devices_active{device_type="traffic_loop"}': "100x40",
            'platform_devices_stale{device_type="traffic_loop"}': "5x40",
            'platform_devices_never_reported{device_type="traffic_loop"}': "0x40",
        },
        "4m",
        False,
    ),
    Case(
        "DeviceTypeOutage",
        "60% silent (stale plus never reported)",
        {
            'platform_devices_active{device_type="traffic_loop"}': "100x40",
            'platform_devices_stale{device_type="traffic_loop"}': "40x40",
            'platform_devices_never_reported{device_type="traffic_loop"}': "20x40",
        },
        "4m",
        True,
        {"device_type": "traffic_loop"},
    ),
    Case(
        "DeviceTypeOutage",
        "30% silent",
        {
            'platform_devices_active{device_type="traffic_loop"}': "100x40",
            'platform_devices_stale{device_type="traffic_loop"}': "30x40",
            'platform_devices_never_reported{device_type="traffic_loop"}': "0x40",
        },
        "4m",
        False,
    ),
    # ---- stream
    Case(
        "GatewayDeliveryStalled",
        "receiving 30 per 15 s, delivering 3",
        {
            "gateway_events_received_total": "0+30x60",
            "gateway_events_delivered_total": "0+3x60",
        },
        "8m",
        True,
    ),
    Case(
        "GatewayDeliveryStalled",
        "delivering everything received",
        {
            "gateway_events_received_total": "0+30x60",
            "gateway_events_delivered_total": "0+30x60",
        },
        "8m",
        False,
    ),
    Case(
        "IngestionRejectionRateHigh",
        "10% schema rejections, no duplicate counter ever emitted",
        {
            "ingestion_events_inserted_total": "0+90x80",
            "ingestion_events_rejected_schema_total": "0+10x80",
        },
        "8m",
        True,
    ),
    Case(
        "IngestionRejectionRateHigh",
        "about 1% rejections",
        {
            "ingestion_events_inserted_total": "0+90x80",
            "ingestion_events_rejected_schema_total": "0+1x80",
        },
        "8m",
        False,
    ),
    Case(
        "IngestionLatencyP95High",
        "40% of observations between 2 s and 5 s",
        histogram(
            "ingestion_ingest_latency_milliseconds",
            {"1000": "0+10x80", "2000": "0+12x80", "5000": "0+20x80", "+Inf": "0+20x80"},
        ),
        "8m",
        True,
    ),
    Case(
        "IngestionLatencyP95High",
        "everything under 1 s",
        histogram(
            "ingestion_ingest_latency_milliseconds",
            {"1000": "0+20x80", "2000": "0+20x80", "5000": "0+20x80", "+Inf": "0+20x80"},
        ),
        "8m",
        False,
    ),
    Case(
        "NetworkStateFreshnessLow",
        "20% fresh",
        {
            'network_state_records_total{freshness_status="fresh"}': "0+2x80",
            'network_state_records_total{freshness_status="stale"}': "0+8x80",
        },
        "8m",
        True,
    ),
    Case(
        "NetworkStateFreshnessLow",
        "nothing fresh at all (no fresh series exists)",
        {'network_state_records_total{freshness_status="stale"}': "0+10x80"},
        "8m",
        True,
    ),
    Case(
        "NetworkStateFreshnessLow",
        "all fresh",
        {'network_state_records_total{freshness_status="fresh"}': "0+20x80"},
        "8m",
        False,
    ),
    # ---- api
    Case(
        "ApiErrorRateHigh",
        "5% of requests are 500",
        {
            'api_http_requests_total{http_status_code="200"}': "0+95x80",
            'api_http_requests_total{http_status_code="500"}': "0+5x80",
        },
        "8m",
        True,
    ),
    Case(
        "ApiErrorRateHigh",
        "no server errors",
        {'api_http_requests_total{http_status_code="200"}': "0+100x80"},
        "8m",
        False,
    ),
    Case(
        "ApiLatencyP95High",
        "40% of requests slower than 2 s",
        histogram(
            "api_http_request_duration_milliseconds",
            {"1000": "0+10x80", "2000": "0+12x80", "5000": "0+20x80", "+Inf": "0+20x80"},
        ),
        "8m",
        True,
    ),
    Case(
        "ApiLatencyP95High",
        "all requests under 1 s",
        histogram(
            "api_http_request_duration_milliseconds",
            {"1000": "0+20x80", "2000": "0+20x80", "5000": "0+20x80", "+Inf": "0+20x80"},
        ),
        "8m",
        False,
    ),
    # ---- model
    Case("EdgeRuntimeDown", "scrape failing", {f"up{{{EDGE}}}": "0x30"}, "4m", True, EDGE_LABELS),
    Case("EdgeRuntimeDown", "scrape ok", {f"up{{{EDGE}}}": "1x30"}, "4m", False),
    Case(
        "EdgeModelInactive",
        "baseline serving",
        {f"edge_model_active{{{EDGE}}}": "0x30"},
        "3m",
        True,
        EDGE_LABELS,
    ),
    Case(
        "EdgeModelInactive", "model serving", {f"edge_model_active{{{EDGE}}}": "1x30"}, "3m", False
    ),
    Case(
        "EdgeModelErrors",
        "an integrity_mismatch was counted",
        {f'edge_model_errors_total{{code="integrity_mismatch",{EDGE}}}': "0 1x30"},
        "2m",
        True,
        {"code": "integrity_mismatch", **EDGE_LABELS},
    ),
    Case(
        "EdgeModelErrors",
        "model failed while the runtime started: counter is already 1 at the first scrape",
        {
            f'edge_model_errors_total{{code="integrity_mismatch",{EDGE}}}': "1x30",
            f"edge_uptime_seconds{{{EDGE}}}": "5+15x30",
        },
        "2m",
        True,
        {"code": "integrity_mismatch", **EDGE_LABELS},
    ),
    Case(
        "EdgeModelErrors",
        "an old error in a long-running process",
        {
            f'edge_model_errors_total{{code="integrity_mismatch",{EDGE}}}': "1x30",
            f"edge_uptime_seconds{{{EDGE}}}": "100000x30",
        },
        "2m",
        False,
    ),
    Case(
        "EdgeModelErrors",
        "no errors",
        {f'edge_model_errors_total{{code="integrity_mismatch",{EDGE}}}': "0x30"},
        "2m",
        False,
    ),
    Case(
        "EdgeModelLatencyP95High",
        "all inferences between 100 and 250 ms",
        histogram(
            "edge_model_inference_latency_ms",
            {"50": "0x60", "100": "0x60", "250": "0+10x60", "+Inf": "0+10x60"},
        ),
        "5m",
        True,
    ),
    Case(
        "EdgeModelLatencyP95High",
        "all inferences under 50 ms",
        histogram(
            "edge_model_inference_latency_ms",
            {"50": "0+10x60", "100": "0+10x60", "250": "0+10x60", "+Inf": "0+10x60"},
        ),
        "5m",
        False,
    ),
    Case(
        "EdgePendingDevicesSaturated",
        "80 devices waiting",
        {f"edge_pending_devices{{{EDGE}}}": "80x30"},
        "3m",
        True,
        EDGE_LABELS,
    ),
    Case(
        "EdgePendingDevicesSaturated",
        "10 devices waiting",
        {f"edge_pending_devices{{{EDGE}}}": "10x30"},
        "3m",
        False,
    ),
    # ---- storage
    Case(
        "DatabaseSizeNearLimit",
        "90% of budget",
        {
            "platform_database_size_bytes": "9000000000x60",
            "platform_database_size_limit_bytes": "10000000000x60",
        },
        "7m",
        True,
    ),
    Case(
        "DatabaseSizeNearLimit",
        "10% of budget",
        {
            "platform_database_size_bytes": "1000000000x60",
            "platform_database_size_limit_bytes": "10000000000x60",
        },
        "7m",
        False,
    ),
    Case(
        "DatabaseSizeCritical",
        "110% of budget",
        {
            "platform_database_size_bytes": "11000000000x60",
            "platform_database_size_limit_bytes": "10000000000x60",
        },
        "3m",
        True,
    ),
    Case(
        "DatabaseSizeCritical",
        "90% of budget",
        {
            "platform_database_size_bytes": "9000000000x60",
            "platform_database_size_limit_bytes": "10000000000x60",
        },
        "3m",
        False,
    ),
    Case(
        "RetentionOverdue",
        "last run 27.7 h ago",
        {"platform_retention_last_run_age_seconds": "100000x60"},
        "7m",
        True,
    ),
    Case(
        "RetentionOverdue",
        "last run 1 h ago",
        {"platform_retention_last_run_age_seconds": "3600x60"},
        "7m",
        False,
    ),
    Case(
        "RetentionUnderStoragePressure",
        "last run under pressure",
        {"platform_retention_storage_pressure": "1x20"},
        "3m",
        True,
    ),
    Case(
        "RetentionUnderStoragePressure",
        "last run normal",
        {"platform_retention_storage_pressure": "0x20"},
        "3m",
        False,
    ),
    # ---- cert
    Case(
        "CertificateExpiringSoon",
        "10 days left",
        {'platform_certificate_expiry_seconds{service_name="postgres"}': "864000x20"},
        "3m",
        True,
        {"service_name": "postgres"},
    ),
    Case(
        "CertificateExpiringSoon",
        "23 days left",
        {'platform_certificate_expiry_seconds{service_name="postgres"}': "2000000x20"},
        "3m",
        False,
    ),
    Case(
        "CertificateExpiringSoon",
        "1 day left is critical, not this warning",
        {'platform_certificate_expiry_seconds{service_name="postgres"}': "86400x20"},
        "3m",
        False,
    ),
    Case(
        "CertificateExpiryCritical",
        "1 day left",
        {'platform_certificate_expiry_seconds{service_name="postgres"}': "86400x20"},
        "3m",
        True,
        {"service_name": "postgres"},
    ),
    Case(
        "CertificateExpiryCritical",
        "already expired",
        {'platform_certificate_expiry_seconds{service_name="mqtt-broker"}': "-100x20"},
        "3m",
        True,
        {"service_name": "mqtt-broker"},
    ),
    Case(
        "CertificateExpiryCritical",
        "10 days left",
        {'platform_certificate_expiry_seconds{service_name="postgres"}': "864000x20"},
        "3m",
        False,
    ),
    Case(
        "DeviceCertificateExpiring",
        "5 days left",
        {'platform_device_certificate_expiry_seconds{device_type="traffic_loop"}': "432000x20"},
        "3m",
        True,
        {"device_type": "traffic_loop"},
    ),
    Case(
        "DeviceCertificateExpiring",
        "23 days left",
        {'platform_device_certificate_expiry_seconds{device_type="traffic_loop"}': "2000000x20"},
        "3m",
        False,
    ),
    # ---- config
    Case(
        "PolicyVersionDrift",
        "engine serves another version",
        {
            "platform_config_policy_version_match": "0x20",
            'platform_probe_target_up{service_name="opa"}': "1x20",
        },
        "3m",
        True,
    ),
    Case(
        "PolicyVersionDrift",
        "engine down: that is PlatformTargetDown, not drift",
        {
            "platform_config_policy_version_match": "0x20",
            'platform_probe_target_up{service_name="opa"}': "0x20",
        },
        "3m",
        False,
    ),
    Case(
        "PolicyVersionDrift",
        "versions match",
        {
            "platform_config_policy_version_match": "1x20",
            'platform_probe_target_up{service_name="opa"}': "1x20",
        },
        "3m",
        False,
    ),
    Case(
        "MigrationDrift",
        "an applied migration's file changed",
        {
            "platform_config_migrations_pending": "0x20",
            "platform_config_migrations_checksum_mismatch": "1x20",
        },
        "3m",
        True,
    ),
    Case(
        "MigrationDrift",
        "two migrations pending",
        {
            "platform_config_migrations_pending": "2x20",
            "platform_config_migrations_checksum_mismatch": "0x20",
        },
        "3m",
        True,
    ),
    Case(
        "MigrationDrift",
        "schema matches the files",
        {
            "platform_config_migrations_pending": "0x20",
            "platform_config_migrations_checksum_mismatch": "0x20",
        },
        "3m",
        False,
    ),
    Case(
        "ApiAuthenticationNotEnforced",
        "auth_mode is off",
        {"platform_config_api_auth_mode_oidc": "0x20"},
        "2m",
        True,
    ),
    Case(
        "ApiAuthenticationNotEnforced",
        "auth_mode is oidc",
        {"platform_config_api_auth_mode_oidc": "1x20"},
        "2m",
        False,
    ),
]


def _render(text: str, labels: dict[str, str]) -> str:
    return re.sub(r"\{\{\s*\$labels\.(\w+)\s*\}\}", lambda m: labels[m.group(1)], text)


def build() -> str:
    rules = {
        r["alert"]: r
        for group in yaml.safe_load(RULES.read_text(encoding="utf-8"))["groups"]
        for r in group["rules"]
    }
    missing = sorted(set(rules) - {c.alert for c in CASES})
    if missing:
        raise SystemExit(f"alerts with no test case: {missing}")
    for alert in rules:
        mine = [c for c in CASES if c.alert == alert]
        if alert != "AlertPipelineWatchdog" and not (
            any(c.fires for c in mine) and any(not c.fires for c in mine)
        ):
            raise SystemExit(f"{alert} needs at least one firing and one healthy case")

    tests = []
    for case in CASES:
        rule = rules[case.alert]
        exp = []
        if case.fires:
            labels = {**rule["labels"], **case.extra}
            exp.append(
                {
                    "exp_labels": labels,
                    "exp_annotations": {
                        k: _render(v, labels) for k, v in rule["annotations"].items()
                    },
                }
            )
        tests.append(
            {
                "interval": "15s",
                "input_series": [{"series": s, "values": v} for s, v in case.series.items()],
                "alert_rule_test": [
                    {"eval_time": case.eval_time, "alertname": case.alert, "exp_alerts": exp}
                ],
            }
        )
    header = (
        "# GENERATED by build_rule_tests.py from rules/aiops-alerts.yml - do not edit; run "
        "`promtool test rules` on it (backend/aiops/verify_alerts.py does).\n"
    )
    doc = {
        "rule_files": ["../rules/aiops-alerts.yml"],
        "evaluation_interval": "15s",
        "tests": tests,
    }
    return header + yaml.safe_dump(doc, sort_keys=False, width=200, allow_unicode=True)


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if current.replace("\r\n", "\n") != text:
            print(
                "rule-tests/aiops-alerts.test.yml is stale; run build_rule_tests.py",
                file=sys.stderr,
            )
            return 1
        print("rule tests are current")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
