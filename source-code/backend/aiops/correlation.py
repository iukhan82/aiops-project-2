"""P10.07: correlate platform signals into incidents. Pure logic: signals in, incident drafts out, no I/O.

A signal is one thing that looks wrong about the platform: an alert that fired (P10.04) or an anomaly the operational
detector raised (P10.06). Left alone they page an operator once per symptom - a database outage alone produces a probe
alert, API errors, API latency, a stale network state and a stalled gateway. This module groups them.

What the grouping rests on, and nothing else:

1. Same component. Every signal is attributed to one component of the platform (the nodes of `backend/topology.json`,
   plus `devices`, `observability` and `platform`). Signals about the same component, from any source, are one incident:
   an "ingestion latency" alert and the detector's anomaly on the ingestion latency signal are one duplicate symptom.
2. Dependency and data flow. A signal that says a component cannot be reached (`unavailable`) or has stopped passing data
   on (`data_loss`) can explain a symptom on a component that depends on it or sits downstream of it, if the symptom is one
   that such a failure can produce (`caused_by`) and it began no earlier than `ONSET_SKEW_S` before the cause. A symptom
   with an explainer joins the incident of its root; a signal with none is a root and keeps its own component's incident.
   Failures that nothing links (a certificate expiring, a configuration drift) stay separate incidents.

What it does NOT do: it never states a cause as fact. Every incident carries a hypothesis with `"verified": false`, the
signals and the graph edges it was inferred from, and the database refuses a hypothesis that says otherwise. A cause
becomes fact only when a person records `verified_cause`.

The dependency edges come from `backend/topology.json` (cross-checked against the services' own source by
`verify_slos.py`). `DATA_FLOW` adds the producer-to-consumer order that topology does not draw.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY_PATH = SOURCE_ROOT / "backend" / "topology.json"

ONSET_SKEW_S = 60  # a cause may be noticed this much later than its first symptom (probe and evaluation cadence)
RESOLVE_AFTER_S = 180  # an incident resolves once every one of its signals has been quiet this long
REOPEN_WITHIN_S = 900  # a signal returning this soon after a resolve reopens the incident instead of opening a new one

# Effects, from most to least able to explain other signals.
EFFECTS = ("unavailable", "data_loss", "degraded", "advisory", "blind")
_EFFECT_RANK = {name: i for i, name in enumerate(EFFECTS)}
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEVERITY_OF_ALERT = {"none": "low", "info": "low", "warning": "medium", "critical": "high"}

# The platform's producer -> consumer order (device readings enter through the broker and end up served by the API).
DATA_FLOW: tuple[tuple[str, str], ...] = (
    ("devices", "mosquitto"),
    ("mosquitto", "gateway"),
    ("gateway", "kafka"),
    ("kafka", "ingestion"),
    ("ingestion", "postgres"),
    ("postgres", "api"),
)
# Dependencies topology.json does not carry: field devices need the broker to publish.
EXTRA_DEPENDS_ON: tuple[tuple[str, str], ...] = (("devices", "mosquitto"),)
PSEUDO_COMPONENTS = ("devices", "observability", "platform")
# service_name values the probe and the certificate gauges use -> component
SERVICE_COMPONENT = {
    "postgres": "postgres",
    "opa": "opa",
    "api": "api",
    "mqtt-broker": "mosquitto",
    "mqtt-devices": "devices",
}


def _load_topology() -> tuple[set[str], set[tuple[str, str]]]:
    topology = json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))
    nodes = {n["id"] for n in topology["nodes"]}
    edges = {(e["from"], e["to"]) for e in topology["edges"]}
    return nodes, edges


_TOPOLOGY_NODES, _TOPOLOGY_EDGES = _load_topology()
COMPONENTS = frozenset(_TOPOLOGY_NODES | set(PSEUDO_COMPONENTS))
DEPENDS_ON = frozenset(_TOPOLOGY_EDGES | set(EXTRA_DEPENDS_ON))  # (client, server)


@dataclass(frozen=True)
class AlertModel:
    """How one alert is read: which component it is about, what kind of failure it reports, and which upstream failure
    kinds could have produced it."""

    component: (
        str  # a component, or "service_name" to take it from the alert's `service_name` label
    )
    effect: str
    cause: str
    caused_by: frozenset[str] = frozenset()


_UD = frozenset({"unavailable", "data_loss"})
_U = frozenset({"unavailable"})

ALERT_MODEL: dict[str, AlertModel] = {
    "PlatformProbeStale": AlertModel(
        "observability",
        "blind",
        "the platform probe has stopped reporting, so the gauges it feeds are out of date",
    ),
    "PlatformProbeMissing": AlertModel(
        "observability",
        "blind",
        "no platform probe has ever reported, so nothing it measures can be trusted",
    ),
    "PlatformTargetDown": AlertModel(
        "service_name", "unavailable", "the platform probe cannot reach {service}"
    ),
    "DevicesSilent": AlertModel("devices", "data_loss", "field devices have stopped reporting", _U),
    "DeviceTypeOutage": AlertModel(
        "devices", "data_loss", "every device of one type has stopped reporting", _U
    ),
    "GatewayDeliveryStalled": AlertModel(
        "gateway",
        "data_loss",
        "the gateway receives events but is not delivering them downstream",
        _U,
    ),
    "IngestionRejectionRateHigh": AlertModel(
        "ingestion", "degraded", "ingestion is rejecting an unusual share of the events it receives"
    ),
    "IngestionLatencyP95High": AlertModel(
        "ingestion", "degraded", "events take unusually long to reach the database", _U
    ),
    "NetworkStateFreshnessLow": AlertModel(
        "ingestion", "degraded", "too little of the computed network state is fresh", _UD
    ),
    "ApiErrorRateHigh": AlertModel(
        "api", "degraded", "the API is answering too many requests with errors", _U
    ),
    "ApiLatencyP95High": AlertModel("api", "degraded", "the API is slow to answer", _U),
    "EdgeRuntimeDown": AlertModel(
        "edge-runtime", "unavailable", "the edge runtime is not answering scrapes"
    ),
    "EdgeModelInactive": AlertModel(
        "edge-runtime", "degraded", "the edge runtime has no active model"
    ),
    "EdgeModelErrors": AlertModel(
        "edge-runtime", "degraded", "the edge runtime is failing to load or run its model"
    ),
    "EdgeModelLatencyP95High": AlertModel(
        "edge-runtime", "degraded", "edge model inference is slow"
    ),
    "EdgePendingDevicesSaturated": AlertModel(
        "edge-runtime", "degraded", "the edge runtime's pending-device buffer is close to full"
    ),
    "DatabaseSizeNearLimit": AlertModel(
        "postgres", "advisory", "the database is close to its size limit"
    ),
    "DatabaseSizeCritical": AlertModel(
        "postgres", "advisory", "the database has reached its size limit"
    ),
    "RetentionOverdue": AlertModel(
        "postgres", "advisory", "the retention job has not run when it should have"
    ),
    "RetentionUnderStoragePressure": AlertModel(
        "postgres", "advisory", "retention is running under storage pressure"
    ),
    "CertificateExpiringSoon": AlertModel(
        "service_name", "advisory", "a {service} certificate expires within 14 days"
    ),
    "CertificateExpiryCritical": AlertModel(
        "service_name", "advisory", "a {service} certificate expires within 3 days"
    ),
    "DeviceCertificateExpiring": AlertModel(
        "devices", "advisory", "a device certificate expires within 14 days"
    ),
    "PolicyVersionDrift": AlertModel(
        "opa", "advisory", "the policy engine serves a different policy version than the repository"
    ),
    "MigrationDrift": AlertModel(
        "postgres",
        "advisory",
        "the applied database migrations differ from the ones in the repository",
    ),
    "ApiAuthenticationNotEnforced": AlertModel(
        "api", "advisory", "the API is running without enforcing authentication"
    ),
}
WATCHDOG = "AlertPipelineWatchdog"

# The detector's signals (`operations_dataset.signals`): component and the upstream failures that could produce a change.
ANOMALY_MODEL: dict[str, tuple[str, frozenset[str]]] = {
    "ingest_rate": ("ingestion", _UD),
    "ingest_reject_ratio": ("ingestion", frozenset()),
    "ingest_latency_p95_ms": ("ingestion", _U),
    "gateway_delivery_ratio": ("gateway", _U),
    "api_request_rate": ("api", frozenset()),
    "api_error_ratio": ("api", _U),
    "api_latency_p95_ms": ("api", _U),
    "state_fresh_share": ("ingestion", _UD),
    "unattributed": ("platform", frozenset()),  # an alarm no single signal accounts for
}

# Labels that identify a signal. Everything else Prometheus adds (instance, job, alert state ...) does not.
SIGNAL_LABELS = ("service_name", "device_type", "code")


@dataclass
class Signal:
    key: str
    source: str  # alert | anomaly
    name: str
    labels: dict[str, str]
    component: str
    effect: str
    cause: str
    caused_by: frozenset[str]
    severity: str  # low | medium | high | critical
    onset: datetime  # when the current activation began
    first_seen: datetime
    last_seen: datetime
    occurrences: int
    active: bool

    def as_row(self) -> dict:
        return {
            "signal_key": self.key,
            "source": self.source,
            "signal": self.name,
            "component": self.component,
            "labels": self.labels,
            "severity": self.severity,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "occurrences": self.occurrences,
            "active": self.active,
        }


@dataclass
class IncidentDraft:
    key: str  # the root component: one live incident per key
    severity: str
    title: str
    components: list[str]
    signals: list[Signal]
    hypothesis: dict
    opened_at: datetime
    updated_at: datetime
    active: bool
    quiet_for_s: float = 0.0


def signal_key(name: str, labels: dict[str, str]) -> str:
    kept = {k: v for k, v in sorted(labels.items()) if k in SIGNAL_LABELS}
    return name + ("{" + ",".join(f"{k}={v}" for k, v in kept.items()) + "}" if kept else "")


def alert_signal(
    name: str,
    labels: dict[str, str],
    severity_label: str,
    onset: datetime,
    first_seen: datetime,
    last_seen: datetime,
    occurrences: int,
    active: bool,
) -> Signal | None:
    """One alert (as Prometheus labels it) read through `ALERT_MODEL`; None for the watchdog. An alert this module does
    not know is not dropped: it becomes a `platform` signal with effect `degraded`, so a new rule can never vanish."""
    if name == WATCHDOG:
        return None
    model = ALERT_MODEL.get(name)
    kept = {k: v for k, v in labels.items() if k in SIGNAL_LABELS}
    if model is None:
        component, effect, cause, caused_by = (
            "platform",
            "degraded",
            f"alert {name} fired",
            frozenset(),
        )
    else:
        component = (
            SERVICE_COMPONENT.get(labels.get("service_name", ""), "platform")
            if model.component == "service_name"
            else model.component
        )
        effect, caused_by = model.effect, model.caused_by
        cause = model.cause.format(service=labels.get("service_name", "an unnamed service"))
    return Signal(
        key=signal_key(name, kept),
        source="alert",
        name=name,
        labels=kept,
        component=component,
        effect=effect,
        cause=cause,
        caused_by=caused_by,
        severity=SEVERITY_OF_ALERT.get(severity_label, "medium"),
        onset=onset,
        first_seen=first_seen,
        last_seen=last_seen,
        occurrences=occurrences,
        active=active,
    )


def anomaly_signal(
    signal_name: str,
    deviation: float,
    onset: datetime,
    first_seen: datetime,
    last_seen: datetime,
    occurrences: int,
    active: bool,
) -> Signal:
    component, caused_by = ANOMALY_MODEL[signal_name]
    return Signal(
        key=f"operations_anomaly{{signal={signal_name}}}",
        source="anomaly",
        name="operations_anomaly",
        labels={"signal": signal_name},
        component=component,
        effect="degraded",
        cause=(
            "the operational detector raised an alarm that no single signal explains"
            if signal_name == "unattributed"
            else f"the operational detector flags {signal_name} as unlike normal ({deviation:.1f} robust deviations)"
        ),
        caused_by=caused_by,
        severity="medium",
        onset=onset,
        first_seen=first_seen,
        last_seen=last_seen,
        occurrences=occurrences,
        active=active,
    )


# ---------------------------------------------------------------------------------------------------- the graph
def _neighbours(effect: str) -> dict[str, list[tuple[str, str]]]:
    """component -> [(component that may show symptoms, why)] for a failure of kind `effect`."""
    out: dict[str, list[tuple[str, str]]] = {}
    for producer, consumer in DATA_FLOW:
        out.setdefault(producer, []).append((consumer, f"{consumer} receives data from {producer}"))
    if effect == "unavailable":
        for client, server in sorted(DEPENDS_ON):
            out.setdefault(server, []).append((client, f"{client} depends on {server}"))
    return out


def reach(component: str, effect: str) -> dict[str, list[str]]:
    """Every component a failure of this kind at `component` can reach, with the shortest chain of reasons."""
    graph = _neighbours(effect)
    paths: dict[str, list[str]] = {}
    queue: deque[str] = deque([component])
    seen = {component}
    while queue:
        here = queue.popleft()
        for there, why in graph.get(here, []):
            if there in seen:
                continue
            seen.add(there)
            paths[there] = [*paths.get(here, []), why]
            queue.append(there)
    return paths


def explains(cause: Signal, symptom: Signal) -> bool:
    if cause.key == symptom.key or cause.component == symptom.component:
        return False
    if cause.effect not in symptom.caused_by:
        return False
    if symptom.component not in reach(cause.component, cause.effect):
        return False
    skew = timedelta(seconds=ONSET_SKEW_S)
    return cause.onset <= symptom.onset + skew and symptom.onset <= cause.last_seen + skew


def _max_severity(signals: list[Signal]) -> str:
    return max((s.severity for s in signals), key=_SEVERITY_RANK.__getitem__)


def correlate(signals: list[Signal], now: datetime) -> list[IncidentDraft]:
    """Group `signals` (as they stand at `now`) into incident drafts, one per root component, most severe first."""
    unique: dict[str, Signal] = {}
    for s in signals:  # the same signal listed twice (two label sets that differ only in ignored labels) is one signal
        prior = unique.get(s.key)
        if prior is None:
            unique[s.key] = s
        else:
            unique[s.key] = Signal(
                **{
                    **prior.__dict__,
                    "onset": min(prior.onset, s.onset),
                    "first_seen": min(prior.first_seen, s.first_seen),
                    "last_seen": max(prior.last_seen, s.last_seen),
                    "occurrences": max(prior.occurrences, s.occurrences),
                    "active": prior.active or s.active,
                }
            )
    ordered = sorted(unique.values(), key=lambda s: (s.onset, s.key))

    parent: dict[str, Signal] = {}
    for s in ordered:
        candidates = [
            c
            for c in ordered
            if explains(c, s) and not (explains(s, c) and (c.onset, c.key) > (s.onset, s.key))
        ]
        if candidates:
            parent[s.key] = min(candidates, key=lambda c: (c.onset, c.key))
    for s in (
        ordered
    ):  # a cycle can only form inside the skew window; its earliest member becomes the root
        walked: list[str] = []
        cur = s
        while cur.key in parent and cur.key not in walked:
            walked.append(cur.key)
            cur = parent[cur.key]
        if cur.key in walked:
            cycle = walked[walked.index(cur.key) :]
            del parent[min(cycle, key=lambda k: (unique[k].onset, k))]

    groups: dict[str, list[Signal]] = {}
    roots: dict[str, list[Signal]] = {}
    via: dict[str, tuple[Signal, list[str]]] = {}
    for s in ordered:
        reasons: list[str] = []
        cur = s
        while cur.key in parent:
            up = parent[cur.key]
            reasons = [*reach(up.component, up.effect)[cur.component], *reasons]
            cur = up
        groups.setdefault(cur.component, []).append(s)
        if cur.key == s.key:
            roots.setdefault(cur.component, []).append(s)
        else:
            via[s.key] = (cur, reasons)

    drafts = [
        _draft(component, members, roots[component], via, now)
        for component, members in groups.items()
    ]
    return sorted(drafts, key=lambda d: (-_SEVERITY_RANK[d.severity], d.opened_at, d.key))


def _draft(
    component: str,
    members: list[Signal],
    roots: list[Signal],
    via: dict[str, tuple[Signal, list[str]]],
    now: datetime,
) -> IncidentDraft:
    primary = min(
        roots,
        key=lambda s: (_EFFECT_RANK[s.effect], -_SEVERITY_RANK[s.severity], s.onset, s.key),
    )
    downstream = sorted({s.component for s in members if s.component != component})
    components = [component, *downstream]
    severity = _max_severity(members)
    if any(s.effect == "unavailable" for s in roots) or (
        len(components) >= 3 and _SEVERITY_RANK[severity] >= _SEVERITY_RANK["high"]
    ):
        severity = "critical"
    updated_at = max(s.last_seen for s in members)
    active = any(s.active for s in members)
    quiet_for = 0.0 if active else max(0.0, (now - updated_at).total_seconds())
    sources = sorted({s.source for s in members})
    n = len(members)
    title = f"{component}: {primary.cause}"
    if n > 1:
        title += f" (+{n - 1} related signal{'s' if n > 2 else ''})"
    hypothesis = {
        "verified": False,
        "kind": "inferred_from_signals_and_dependency_graph",
        "suspected_root_component": component,
        "suspected_cause": primary.cause,
        "root_signals": [s.key for s in sorted(roots, key=lambda s: (s.onset, s.key))],
        "downstream": [
            {
                "signal": s.key,
                "component": s.component,
                "because": via[s.key][1],
                "explained_by": via[s.key][0].key,
            }
            for s in members
            if s.key in via
        ],
        "corroborated_by_sources": sources,
        "onset_order": [
            {"signal": s.key, "onset": s.onset.isoformat()}
            for s in sorted(members, key=lambda s: (s.onset, s.key))
        ],
        "note": "An inference from alerts, the detector and the dependency graph. Nothing here has been confirmed.",
    }
    return IncidentDraft(
        key=component,
        severity=severity,
        title=title[:300],
        components=components,
        signals=sorted(members, key=lambda s: (s.onset, s.key)),
        hypothesis=hypothesis,
        opened_at=min(s.first_seen for s in members),
        updated_at=updated_at,
        active=active,
        quiet_for_s=quiet_for,
    )


__all__ = [
    "ALERT_MODEL",
    "ANOMALY_MODEL",
    "COMPONENTS",
    "IncidentDraft",
    "Signal",
    "alert_signal",
    "anomaly_signal",
    "correlate",
    "explains",
    "reach",
    "signal_key",
]
