"""P04.05: the edge runtime - validated intake, past-only features, verified
ONNX inference with abstention, transparent-baseline fallback, bounded
metrics and health.

Safety posture (docs/PROJECT_CONTEXT.md, ADR-0005):

- Nothing unvalidated reaches features (EdgeValidator gates every event).
- The model is served only if its package verified at startup
  (model_runtime.load_package). Any ModelError, or repeated inference
  errors, drops the runtime to the P04.02 rule baseline - it never serves an
  unverified model and never goes silent.
- Insufficient/degraded evidence yields an explicit abstention with a
  reason, not a guess. If neither model nor baseline is available the
  runtime reports not-ready and abstains ("no_decision_path").
- Outputs are recommendations/candidates (`truth_label: inferred`); the
  runtime has no actuator.
- Everything is bounded: fixed-size histories, fixed metric label sets,
  one ONNX thread, no unbounded queues.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

import numpy as np

from edge import observability as obs
from edge.baseline import BaselineError, RuleBaseline
from edge.features import FEATURE_VERSION, FeatureBuilder, FeatureConfig, FeatureResult
from edge.metrics import MetricsRegistry
from edge.model_runtime import ModelError, OnnxModel, load_package
from edge.timeutil import format_ts, parse_ts
from edge.topology import loop_topology
from edge.validation import (
    FLAG_STEMS,
    REJECT_REASONS,
    DeviceRegistry,
    EdgeValidator,
    ValidationConfig,
    Verdict,
)

RUNTIME_VERSION = "1.0.0"
INFERENCE_EVENT_TYPE = "edge.inference.blockage_candidate"
RULE_CONFIDENCE = 0.5  # rule evidence is not a calibrated probability
MAX_SOURCE_EVENT_IDS = 24
EVENT_NAMESPACE = uuid.UUID("6f6f6f6f-0405-4a4a-8a8a-202609190005")
ABSTAIN_REASONS = (
    "insufficient_data",
    "uncertain_probability",
    "model_inference_error",
    "no_decision_path",
)


class EventSink(Protocol):
    def emit(self, event: dict) -> None: ...


class SequenceSource(Protocol):
    def next(self, device_id: str) -> int: ...


class ListSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: dict) -> None:
        self.events.append(event)


class MemorySequence:
    def __init__(self) -> None:
        self._n: dict[str, int] = {}

    def next(self, device_id: str) -> int:
        value = self._n.get(device_id, 0)
        self._n[device_id] = value + 1
        return value


@dataclass(frozen=True)
class EdgeConfig:
    site_id: str
    runtime_device_id: str
    geometry_version: str
    registry_path: Path
    baseline_path: Path
    model_dir: Path | None = None
    eval_delay_s: float = 1.0
    emit_clear: bool = False
    boot_id: str | None = None  # fixed in tests/replays for deterministic event ids
    liveness_timeout_s: float = 60.0
    max_consecutive_model_errors: int = 5
    capture_latency_samples: bool = False  # benchmarking only; unbounded otherwise
    feature: FeatureConfig = field(default_factory=FeatureConfig)

    def config_hash(self) -> str:
        blob = json.dumps(
            {
                "site": self.site_id,
                "geometry": self.geometry_version,
                "eval_delay_s": self.eval_delay_s,
                "emit_clear": self.emit_clear,
                "feature": self.feature.__dict__,
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()


@dataclass(frozen=True)
class Inference:
    device_id: str
    as_of: datetime
    decision: str  # alarm | abstain | clear
    mode: str  # model | baseline | none
    probability: float | None
    abstain_reason: str | None
    feature_quality: str
    latency_ms: float | None
    source_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Health:
    live: bool
    ready: bool
    degraded: bool
    mode: str
    reasons: tuple[str, ...]
    model: str | None
    uptime_s: float

    def to_dict(self) -> dict:
        return {
            "live": self.live,
            "ready": self.ready,
            "degraded": self.degraded,
            "mode": self.mode,
            "reasons": list(self.reasons),
            "model": self.model,
            "uptime_s": round(self.uptime_s, 3),
        }


def resident_memory_bytes() -> int | None:
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
        return pages * 4096
    except (OSError, ValueError, IndexError):
        return None


class EdgeRuntime:
    def __init__(
        self,
        config: EdgeConfig,
        registry: DeviceRegistry,
        baseline: RuleBaseline | None,
        model: OnnxModel | None,
        sink: EventSink,
        sequence: SequenceSource | None = None,
        startup_problems: tuple[str, ...] = (),
    ) -> None:
        self.config = config
        self.registry = registry
        self.baseline = baseline
        self._model = model
        self.sink = sink
        self.sequence = sequence or MemorySequence()
        self.startup_problems = list(startup_problems)
        self.model_disabled_reason: str | None = None
        self.validator = EdgeValidator(
            registry, ValidationConfig(expected_geometry_version=config.geometry_version)
        )
        self.features = FeatureBuilder(config.feature, loop_topology(registry))
        self._pending: dict[str, datetime] = {}
        self._consecutive_model_errors = 0
        self._boot_id = config.boot_id or uuid.uuid4().hex
        self._started = time.monotonic()
        self._last_heartbeat = self._started
        self.sample_model_ms: list[float] = []
        self.sample_decision_ms: list[float] = []
        self._init_metrics()
        self._sync_model_metrics()

    # -- construction -----------------------------------------------------------------

    @classmethod
    def build(
        cls,
        config: EdgeConfig,
        sink: EventSink,
        sequence: SequenceSource | None = None,
    ) -> EdgeRuntime:
        """Load registry (required), baseline and model (both degradable)."""
        registry = DeviceRegistry.from_jsonl(config.registry_path)
        problems: list[str] = []
        baseline = None
        try:
            baseline = RuleBaseline.load(config.baseline_path, FEATURE_VERSION)
        except BaselineError as exc:
            problems.append(f"baseline_unavailable:{exc}")
        model = None
        if config.model_dir is not None:
            try:
                model = load_package(config.model_dir, expected_feature_version=FEATURE_VERSION)
            except ModelError as exc:
                problems.append(f"model_unavailable:{exc.code}")
        runtime = cls(config, registry, baseline, model, sink, sequence, tuple(problems))
        for problem in problems:
            if problem.startswith("model_unavailable:"):
                runtime.metrics_model_errors.inc(code=problem.split(":", 1)[1])
        return runtime

    def _init_metrics(self) -> None:
        m = MetricsRegistry()
        self.metrics = m
        self.metrics_events = m.counter(
            "edge_events_total",
            "Events by validation outcome",
            {"status": ["accepted", "accepted_with_flags", "rejected"]},
        )
        self.metrics_rejected = m.counter(
            "edge_events_rejected_total", "Rejected events by reason", {"reason": REJECT_REASONS}
        )
        self.metrics_flags = m.counter(
            "edge_events_flagged_total", "Accepted-with-flags events by flag", {"flag": FLAG_STEMS}
        )
        self.metrics_inference = m.counter(
            "edge_inference_total",
            "Decisions by outcome and serving mode",
            {"decision": ["alarm", "abstain", "clear"], "mode": ["model", "baseline", "none"]},
        )
        self.metrics_abstain = m.counter(
            "edge_abstentions_total", "Abstentions by reason", {"reason": ABSTAIN_REASONS}
        )
        self.metrics_quality = m.counter(
            "edge_feature_quality_total",
            "Feature vectors by quality",
            {"quality": ["ok", "degraded", "insufficient"]},
        )
        self.metrics_model_errors = m.counter(
            "edge_model_errors_total",
            "Model load/inference failures by code",
            {
                "code": [
                    "artifact_missing",
                    "integrity_mismatch",
                    "schema_mismatch",
                    "load_failed",
                    "golden_mismatch",
                    "inference_failed",
                    "consecutive_errors",
                ]
            },
        )
        self.metrics_emitted = m.counter("edge_events_emitted_total", "Derived events emitted")
        self.hist_model = m.histogram(
            "edge_model_inference_latency_ms", "ONNX predict latency (per vector)"
        )
        self.hist_tick = m.histogram(
            "edge_decision_latency_ms", "Feature build + decision + emit latency (per device)"
        )
        self.gauge_pending = m.gauge("edge_pending_devices", "Devices awaiting evaluation")
        self.gauge_model_active = m.gauge("edge_model_active", "1 if the ONNX model is serving")
        self.gauge_model_info = m.gauge(
            "edge_model_info",
            "Serving model identity (value 1)",
            {"model": [self._model.identity.label] if self._model else ["none"]},
        )
        self.gauge_memory = m.gauge("edge_process_resident_memory_bytes", "Process RSS")
        self.gauge_uptime = m.gauge("edge_uptime_seconds", "Runtime uptime")

    def _sync_model_metrics(self) -> None:
        self.gauge_model_active.set(1.0 if self._model else 0.0)
        label = self._model.identity.label if self._model else "none"
        self.gauge_model_info.set(1.0, model=label)

    # -- model slot ---------------------------------------------------------------------

    @property
    def model(self) -> OnnxModel | None:
        return self._model

    def swap_model(self, model: OnnxModel | None, reason: str | None = None) -> None:
        """Single-reference swap: in-flight evaluation finishes on the old object."""
        self._model = model
        self.model_disabled_reason = reason if model is None else None
        self._consecutive_model_errors = 0
        if model is not None:
            self.gauge_model_info.allowed["model"] = frozenset(
                self.gauge_model_info.allowed["model"] | {model.identity.label}
            )
        self._sync_model_metrics()

    def disable_model(self, reason: str) -> None:
        self.swap_model(None, reason)

    # -- intake -------------------------------------------------------------------------

    def heartbeat(self) -> None:
        self._last_heartbeat = time.monotonic()

    def ingest(self, raw: object, now: datetime, transport_identity: str | None = None) -> Verdict:
        self.heartbeat()
        verdict = self.validator.validate(raw, now, transport_identity)
        self.metrics_events.inc(status=verdict.status.value)
        if verdict.reasons:
            for reason in verdict.reasons:
                self.metrics_rejected.inc(reason=reason)
        for flag in verdict.flags:
            self.metrics_flags.inc(flag=flag.split(":", 1)[0])
        if self.features.update(verdict) and verdict.event is not None:
            observed = parse_ts(verdict.event["observation_time"])
            device_id = verdict.event["device_id"]
            if device_id not in self._pending or observed > self._pending[device_id]:
                self._pending[device_id] = observed
        self.gauge_pending.set(len(self._pending))
        return verdict

    # -- decisions ----------------------------------------------------------------------

    def evaluate_due(self, now: datetime) -> list[Inference]:
        self.heartbeat()
        delay = timedelta(seconds=self.config.eval_delay_s)
        due = sorted(d for d, t in self._pending.items() if t + delay <= now)
        results = []
        for device_id in due:
            as_of = self._pending.pop(device_id)
            results.append(self._evaluate_device(device_id, as_of, now))
        self.gauge_pending.set(len(self._pending))
        return results

    def _evaluate_device(self, device_id: str, as_of: datetime, now: datetime) -> Inference:
        correlation_id = f"{self.config.site_id}:{device_id}:{format_ts(as_of)}"
        with obs.span(
            "edge.evaluate_device",
            correlation_id=correlation_id,
            device_id=device_id,
        ):
            started = time.perf_counter()
            result = self.features.build(device_id, as_of)
            self.metrics_quality.inc(quality=result.quality)
            inference = self._decide(result)
            self.metrics_inference.inc(decision=inference.decision, mode=inference.mode)
            if inference.abstain_reason:
                self.metrics_abstain.inc(reason=inference.abstain_reason)
            if inference.decision != "clear" or self.config.emit_clear:
                self.sink.emit(self._to_event(inference, now))
                self.metrics_emitted.inc()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self.hist_tick.observe(elapsed_ms)
            if self.config.capture_latency_samples:
                self.sample_decision_ms.append(elapsed_ms)
            return inference

    def _decide(self, result: FeatureResult) -> Inference:
        ids = tuple(result.source_event_ids) + tuple(
            i for ids_ in result.context_event_ids.values() for i in ids_
        )

        def make(decision, mode, probability=None, reason=None, latency=None) -> Inference:
            return Inference(
                result.device_id, result.as_of, decision, mode, probability, reason,
                result.quality, latency, ids,
            )  # fmt: skip

        if result.values is None:
            mode = "model" if self._model else ("baseline" if self.baseline else "none")
            return make("abstain", mode, reason="insufficient_data")

        if self._model is not None:
            try:
                probability, decision, latency_ms = self._model.infer(
                    np.asarray(result.values, dtype=np.float32)
                )
            except ModelError as exc:
                self.metrics_model_errors.inc(code=exc.code)
                self._consecutive_model_errors += 1
                if self._consecutive_model_errors >= self.config.max_consecutive_model_errors:
                    self.metrics_model_errors.inc(code="consecutive_errors")
                    self.disable_model("consecutive_inference_errors")
                return self._baseline_decision(
                    result, make, fallback_reason="model_inference_error"
                )
            self._consecutive_model_errors = 0
            self.hist_model.observe(latency_ms)
            if self.config.capture_latency_samples:
                self.sample_model_ms.append(latency_ms)
            reason = "uncertain_probability" if decision == "abstain" else None
            return make(decision, "model", probability, reason, latency_ms)
        return self._baseline_decision(result, make)

    def _baseline_decision(self, result: FeatureResult, make, fallback_reason: str | None = None):
        if self.baseline is None:
            return make("abstain", "none", reason="no_decision_path")
        decision = self.baseline.evaluate(result.values)
        if decision.alarm:
            return make("alarm", "baseline", probability=None)
        if fallback_reason:  # model failed and the rule sees nothing: stay explicit
            return make("abstain", "baseline", reason=fallback_reason)
        return make("clear", "baseline")

    # -- output events ------------------------------------------------------------------

    def _pipeline_version(self) -> str:
        model = self._model.identity.label if self._model else "none"
        return (
            f"edge-runtime/{RUNTIME_VERSION}+model={model}+features={FEATURE_VERSION}"
            f"+cfg={self.config.config_hash()[:8]}"
        )

    def _to_event(self, inf: Inference, now: datetime) -> dict:
        cfg = self.config
        device = self.registry.get(inf.device_id) or {}
        location = device.get("location", {})
        seq = self.sequence.next(cfg.runtime_device_id)
        abstained = inf.decision == "abstain"
        degraded = inf.feature_quality != "ok"
        quality = "suspect" if degraded else "valid"

        if inf.probability is not None:
            confidence = max(inf.probability, 1.0 - inf.probability)
        else:
            confidence = RULE_CONFIDENCE
        measurements: list[dict] = []
        if inf.probability is not None:
            measurements.append(
                {
                    "name": "blockage_probability",
                    "value": round(inf.probability, 6),
                    "unit": "ratio",
                    "quality": quality,
                    "confidence": round(confidence, 6),
                }
            )
        for name, value, unit in (
            ("decision", inf.decision, "category"),
            ("abstained", abstained, "boolean"),
            ("inference_mode", inf.mode, "category"),
            ("feature_quality", inf.feature_quality, "category"),
        ):
            measurements.append(
                {
                    "name": name,
                    "value": value,
                    "unit": unit,
                    "quality": quality if name == "decision" else "valid",
                    "confidence": round(confidence, 6),
                }
            )
        if inf.abstain_reason:
            measurements.append(
                {
                    "name": "abstain_reason",
                    "value": inf.abstain_reason,
                    "unit": "category",
                    "quality": "valid",
                    "confidence": 1.0,
                }
            )

        loc: dict = {
            "coordinate_reference": "EPSG:4326",
            "latitude": location.get("latitude", 0.0),
            "longitude": location.get("longitude", 0.0),
        }
        for key in ("intersection_id", "corridor_id", "lane_id"):
            if key in location:
                loc[key] = location[key]

        return {
            "schema_version": "1.0.0",
            "event_id": str(
                uuid.uuid5(EVENT_NAMESPACE, f"{cfg.runtime_device_id}:{self._boot_id}:{seq}")
            ),
            "event_type": INFERENCE_EVENT_TYPE,
            "device_id": cfg.runtime_device_id,
            "agency_scope": device.get("agency_scope", "city-traffic-ops"),
            "observation_time": format_ts(inf.as_of),
            "ingest_time": format_ts(max(now, inf.as_of)),
            "sequence_number": seq,
            "clock_quality": "synced",
            "geometry_version": cfg.geometry_version,
            "location": loc,
            "measurements": measurements,
            "truth_label": "inferred",
            "privacy_classification": "none",
            "retention_class": "standard",
            "correlation_id": f"{cfg.site_id}:{inf.device_id}:{format_ts(inf.as_of)}",
            "provenance": {
                "producer": f"edge-runtime/{cfg.site_id}",
                "pipeline_version": self._pipeline_version(),
                "source_event_ids": list(dict.fromkeys(inf.source_event_ids))[
                    :MAX_SOURCE_EVENT_IDS
                ],
            },
        }

    # -- health -------------------------------------------------------------------------

    def health(self) -> Health:
        reasons = list(self.startup_problems)
        if self.model_disabled_reason:
            reasons.append(f"model_disabled:{self.model_disabled_reason}")
        expects_model = self.config.model_dir is not None
        mode = "model" if self._model else ("baseline" if self.baseline else "none")
        if expects_model and self._model is None and "model_unavailable" not in " ".join(reasons):
            reasons.append("model_not_serving")
        live = (time.monotonic() - self._last_heartbeat) <= self.config.liveness_timeout_s
        ready = len(self.registry) > 0 and mode != "none"
        return Health(
            live=live,
            ready=ready,
            degraded=bool(reasons),
            mode=mode,
            reasons=tuple(reasons),
            model=self._model.identity.label if self._model else None,
            uptime_s=time.monotonic() - self._started,
        )

    def render_metrics(self) -> str:
        self.gauge_uptime.set(time.monotonic() - self._started)
        rss = resident_memory_bytes()
        if rss is not None:
            self.gauge_memory.set(rss)
        return self.metrics.render()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


Clock = Callable[[], datetime]
