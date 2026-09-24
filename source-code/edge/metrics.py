"""Tiny dependency-free metrics registry with Prometheus text exposition.

Cardinality is bounded by construction (docs/environment/RESOURCE_BUDGET.md:
"Metrics label sets must be bounded ... vehicle/track IDs never become metric
labels"): every label has an allow-list fixed at registration, and any value
outside it is collapsed to "other" rather than creating a new series.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

LATENCY_BUCKETS_MS: tuple[float, ...] = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 25, 50, 100, 250, 1000)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class _Family:
    def __init__(self, name: str, help_text: str, labels: Mapping[str, Sequence[str]]) -> None:
        self.name = name
        self.help = help_text
        self.allowed = {k: frozenset(v) for k, v in labels.items()}

    def key(self, values: Mapping[str, str]) -> tuple[str, ...]:
        if set(values) != set(self.allowed):
            raise ValueError(f"{self.name}: expected labels {sorted(self.allowed)}")
        return tuple(
            values[name] if values[name] in self.allowed[name] else "other"
            for name in sorted(self.allowed)
        )

    def label_text(self, key: tuple[str, ...], extra: str = "") -> str:
        pairs = [f'{n}="{_escape(v)}"' for n, v in zip(sorted(self.allowed), key, strict=True)]
        if extra:
            pairs.append(extra)
        return "{" + ",".join(pairs) + "}" if pairs else ""


class Counter(_Family):
    def __init__(self, name, help_text, labels):
        super().__init__(name, help_text, labels)
        self.values: dict[tuple[str, ...], float] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        k = self.key(labels)
        self.values[k] = self.values.get(k, 0.0) + amount

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        keys = sorted(self.values) or ([()] if not self.allowed else [])
        for k in keys:
            out.append(f"{self.name}{self.label_text(k) if k else ''} {self.values.get(k, 0.0):g}")
        return out


class Gauge(_Family):
    def __init__(self, name, help_text, labels):
        super().__init__(name, help_text, labels)
        self.values: dict[tuple[str, ...], float] = {}

    def set(self, value: float, **labels: str) -> None:
        self.values[self.key(labels)] = float(value)

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} gauge"]
        keys = sorted(self.values) or ([()] if not self.allowed else [])
        for k in keys:
            out.append(f"{self.name}{self.label_text(k) if k else ''} {self.values.get(k, 0.0):g}")
        return out


class Histogram:
    def __init__(self, name: str, help_text: str, buckets: Sequence[float] = LATENCY_BUCKETS_MS):
        self.name, self.help = name, help_text
        self.buckets = tuple(buckets)
        self.counts = [0] * len(self.buckets)
        self.count = 0
        self.sum = 0.0
        self.max = 0.0

    def observe(self, value: float) -> None:
        self.count += 1
        self.sum += value
        self.max = max(self.max, value)
        for i, bound in enumerate(self.buckets):
            if value <= bound:
                self.counts[i] += 1

    def quantile(self, q: float) -> float:
        """Upper bucket bound containing the q-quantile (conservative)."""
        if not self.count:
            return math.nan
        target = q * self.count
        for bound, cumulative in zip(self.buckets, self.counts, strict=True):
            if cumulative >= target:
                return bound
        return math.inf

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        for bound, cumulative in zip(self.buckets, self.counts, strict=True):
            out.append(f'{self.name}_bucket{{le="{bound:g}"}} {cumulative}')
        out.append(f'{self.name}_bucket{{le="+Inf"}} {self.count}')
        out.append(f"{self.name}_sum {self.sum:g}")
        out.append(f"{self.name}_count {self.count}")
        return out


class MetricsRegistry:
    def __init__(self) -> None:
        self._families: dict[str, Counter | Gauge | Histogram] = {}

    def counter(self, name: str, help_text: str, labels: Mapping[str, Sequence[str]] | None = None):
        self._families[name] = Counter(name, help_text, labels or {})
        return self._families[name]

    def gauge(self, name: str, help_text: str, labels: Mapping[str, Sequence[str]] | None = None):
        self._families[name] = Gauge(name, help_text, labels or {})
        return self._families[name]

    def histogram(self, name: str, help_text: str, buckets: Sequence[float] = LATENCY_BUCKETS_MS):
        self._families[name] = Histogram(name, help_text, buckets)
        return self._families[name]

    def render(self) -> str:
        lines: list[str] = []
        for name in sorted(self._families):
            lines.extend(self._families[name].render())
        return "\n".join(lines) + "\n"

    def series_count(self) -> int:
        total = 0
        for family in self._families.values():
            if isinstance(family, Histogram):
                total += len(family.buckets) + 3
            else:
                total += max(1, len(family.values))
        return total
