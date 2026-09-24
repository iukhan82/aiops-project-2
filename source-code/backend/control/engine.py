"""P07.04: the shared recommendation shape and its hard safety bounds.

Every generator (diversion, signal plan change, and later transit priority /
emergency pre-emption in P07.07/P07.08) produces the same `Alternative` shape
- a description, named+valued+unit `predicted_benefit`/`predicted_harm`
metrics, and a `confidence` - so an operator compares options the same way
regardless of action type (`contracts/recommendation/v1`).

`SafetyBounds` are not advisory: `enforce` clips or drops an alternative that
would violate them rather than merely flagging it, because a recommendation
that *could* be approved unmodified must never itself carry an unsafe number.
P07.05's execution-time policy is a second, independent check at approval
time - this is the first one, at generation time.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    name: str
    value: float
    unit: str

    def as_record(self) -> dict:
        return {"name": self.name, "value": round(self.value, 3), "unit": self.unit}


@dataclass(frozen=True)
class SafetyBounds:
    min_pedestrian_clearance_s: float
    max_signal_deviation_s: float

    def as_record(self) -> dict:
        return {
            "min_pedestrian_clearance_s": self.min_pedestrian_clearance_s,
            "max_signal_deviation_s": self.max_signal_deviation_s,
        }


@dataclass(frozen=True)
class Alternative:
    alternative_id: str
    description: str
    predicted_benefit: tuple[Metric, ...]
    predicted_harm: tuple[Metric, ...]
    confidence: float
    signal_deviation_s: float = 0.0  # 0 for anything that is not a signal-timing change
    pedestrian_clearance_s: float | None = (
        None  # None when the alternative does not touch a pedestrian phase
    )

    def as_record(self) -> dict:
        return {
            "alternative_id": self.alternative_id,
            "description": self.description,
            "predicted_benefit": [m.as_record() for m in self.predicted_benefit],
            "predicted_harm": [m.as_record() for m in self.predicted_harm],
            "confidence": round(self.confidence, 4),
        }


class UnsafeAlternative(Exception):
    """Raised (never silently swallowed) when every candidate alternative for a recommendation
    would violate the safety bounds - a recommendation engine must not emit nothing-but-unsafe
    options disguised as a "do nothing" recommendation with no real content."""


def enforce(alternatives: list[Alternative], bounds: SafetyBounds) -> list[Alternative]:
    """Drops any alternative that violates a bound outright (a deviation cannot be silently
    truncated back into legality - that would report a different action than was evaluated);
    what remains is what may legally be proposed. Raises UnsafeAlternative if that leaves none."""
    kept = [
        a
        for a in alternatives
        if a.signal_deviation_s <= bounds.max_signal_deviation_s
        and (
            a.pedestrian_clearance_s is None
            or a.pedestrian_clearance_s >= bounds.min_pedestrian_clearance_s
        )
    ]
    if not kept:
        raise UnsafeAlternative(
            f"all {len(alternatives)} candidate alternatives violate safety bounds {bounds.as_record()}"
        )
    return kept


@dataclass
class Recommendation:
    recommendation_id: str
    action_type: str
    alternatives: list[Alternative]
    safety_bounds: SafetyBounds
    constraints: list[str]
    trigger_incident_id: str | None = None
    trigger_emergency_call_id: str | None = None

    def as_record(self, generated_at: str, expires_at: str, status: str = "proposed") -> dict:
        return {
            "schema_version": "1.0.0",
            "recommendation_id": self.recommendation_id,
            "action_type": self.action_type,
            "generated_at": generated_at,
            "expires_at": expires_at,
            "status": status,
            "alternatives": [a.as_record() for a in self.alternatives],
            "safety_bounds": self.safety_bounds.as_record(),
            "constraints": self.constraints,
            **(
                {"trigger_incident_id": self.trigger_incident_id}
                if self.trigger_incident_id
                else {}
            ),
            **(
                {"trigger_emergency_call_id": self.trigger_emergency_call_id}
                if self.trigger_emergency_call_id
                else {}
            ),
        }
