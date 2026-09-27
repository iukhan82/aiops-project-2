"""P10.08: the trace-sampling override a bounded remediation may set.

Every service samples every trace by default. Under telemetry pressure the AIOps remediation worker can lower the share of
NEW traces that are kept, by writing one small JSON file; every process that configured `backend.observability` re-reads it
every few seconds. The override is bounded three ways: the ratio cannot go below `MIN_RATIO`, it carries its own expiry
(`MAX_TTL_S` at most) after which sampling returns to 100 % without anyone doing anything, and a missing, malformed or
expired file means "sample everything" - the safe direction for observability. A trace already started keeps its decision
(`ParentBased`), so a request is never half-traced.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from opentelemetry.sdk.trace.sampling import ParentBased, Sampler, SamplingResult, TraceIdRatioBased

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "infra" / "platform" / "output"
OVERRIDE_PATH = Path(
    os.environ.get("AIOPS_TRACE_SAMPLING_FILE", OUTPUT_DIR / "trace_sampling.json")
)
MIN_RATIO = 0.01
MAX_TTL_S = 900
REFRESH_S = 5.0


def read_override(path: Path = OVERRIDE_PATH, now: float | None = None) -> float:
    """The ratio currently in force: the file's, when it is well formed, unexpired and within bounds; otherwise 1.0."""
    now = time.time() if now is None else now
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ratio, expires_at = float(data["ratio"]), float(data["expires_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return 1.0
    if not (MIN_RATIO <= ratio <= 1.0) or expires_at <= now or expires_at - now > MAX_TTL_S + 60:
        return 1.0
    return ratio


def write_override(
    ratio: float, ttl_s: float, set_by: str, path: Path = OVERRIDE_PATH, now: float | None = None
) -> dict:
    """Atomically write an override. Refuses a ratio or a lifetime outside the bounds instead of clamping silently."""
    now = time.time() if now is None else now
    if not (MIN_RATIO <= ratio <= 1.0):
        raise ValueError(f"ratio {ratio} is outside [{MIN_RATIO}, 1.0]")
    if not (0 < ttl_s <= MAX_TTL_S):
        raise ValueError(f"ttl {ttl_s} s is outside (0, {MAX_TTL_S}]")
    doc = {"ratio": ratio, "expires_at": now + ttl_s, "set_by": set_by, "set_at": now}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(doc, handle)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return doc


def clear_override(path: Path = OVERRIDE_PATH) -> None:
    path.unlink(missing_ok=True)


class _OverridableRoot(Sampler):
    def __init__(
        self,
        path: Path = OVERRIDE_PATH,
        refresh_s: float = REFRESH_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path, self._refresh_s, self._clock = path, refresh_s, clock
        self._checked_at = float("-inf")
        self._ratio = 1.0
        self._delegate = TraceIdRatioBased(1.0)

    def _current(self) -> TraceIdRatioBased:
        now = self._clock()
        if now - self._checked_at >= self._refresh_s:
            self._checked_at = now
            ratio = read_override(self._path, now)
            if ratio != self._ratio:
                self._ratio, self._delegate = ratio, TraceIdRatioBased(ratio)
        return self._delegate

    def should_sample(
        self,
        parent_context,
        trace_id,
        name,
        kind=None,
        attributes=None,
        links=None,
        trace_state=None,
    ) -> SamplingResult:  # noqa: ANN001, PLR0913
        return self._current().should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        return "OverridableRatioSampler"


def sampler(path: Path = OVERRIDE_PATH, refresh_s: float = REFRESH_S) -> Sampler:
    return ParentBased(_OverridableRoot(path, refresh_s))
