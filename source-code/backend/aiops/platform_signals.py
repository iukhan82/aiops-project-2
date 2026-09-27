"""P10.07: read platform signals from Prometheus and turn them into `correlation.Signal`s.

Two sources, one shape:

- Alerts. The `ALERTS` series Prometheus itself records for every pending or firing rule (P10.04). A signal exists once an
  alert has fired; its onset is when the condition first held (the start of the pending period), which is what causal
  ordering needs.
- Anomalies. The eight operational signals of P10.05, queried live with the same PromQL and step as training, scored by the
  packaged detector (P10.06). The alarm rule is the detector's own (score at or above its threshold for 3 consecutive
  steps). The score only says that something moved; which signal moved is read from how far each signal is from the
  training-normal median (`OperationsDetector.deviations`), and an alarm that no signal explains is kept as `unattributed`
  rather than pinned on a guess.

Everything here is a pure function of data already fetched, so a recorded timeline can be replayed step by step through
exactly the code the live correlator runs (`verify_platform_incidents.py` does that with the P10.04 lab window).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import requests

from backend.aiops.correlation import Signal, alert_signal, anomaly_signal
from models.operations_dataset.signals import SIGNALS, WINDOW
from models.operations_detector.evaluate import PERSISTENCE, episodes
from models.operations_detector.features import HISTORY, SIGNAL_NAMES, STEP_S
from models.operations_detector.runtime import OperationsDetector

PROM_URL = os.environ.get("AIOPS_PROMETHEUS_URL", "http://127.0.0.1:9090")
ALERT_STEP_S = 15  # ALERTS is evaluated every 15 s in this stack
ALERT_GAP_STEPS = (
    3  # a series absent for this many steps has ended; a later sample starts a new activation
)
ATTRIBUTION_DEVIATION = 5.0  # robust standard deviations from the training-normal median that count as "this signal moved"
MAX_ATTRIBUTED = 3


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


def _range(query: str, start: datetime, end: datetime, step_s: int, url: str) -> list[dict]:
    response = requests.get(
        f"{url}/api/v1/query_range",
        params={"query": query, "start": start.timestamp(), "end": end.timestamp(), "step": step_s},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["data"]["result"]


# ---------------------------------------------------------------------------------------------------------- alerts
def fetch_alert_series(
    start: datetime, end: datetime, step_s: int = ALERT_STEP_S, url: str = PROM_URL
) -> list[dict]:
    return _range('ALERTS{alertstate=~"pending|firing"}', start, end, step_s, url)


def alert_signals(series: list[dict], now: datetime, step_s: int = ALERT_STEP_S) -> list[Signal]:
    """The alert signals as they stand at `now`, from raw `ALERTS` range-query series. Only samples up to `now` count."""
    now_ts = now.timestamp()
    per_key: dict[tuple, dict] = {}
    for item in series:
        labels = item["metric"]
        name = labels.get("alertname", "")
        kept = {k: v for k, v in labels.items() if k in ("service_name", "device_type", "code")}
        key = (name, tuple(sorted(kept.items())))
        entry = per_key.setdefault(
            key, {"labels": labels, "kept": kept, "samples": {}, "severity": "warning"}
        )
        entry["severity"] = labels.get("severity", entry["severity"])
        for ts, _value in item["values"]:
            if ts > now_ts:
                continue
            state = labels.get("alertstate", "firing")
            previous = entry["samples"].get(ts)
            entry["samples"][ts] = "firing" if "firing" in (state, previous) else "pending"
    out: list[Signal] = []
    for (name, _), entry in per_key.items():
        if not entry["samples"]:
            continue
        stamps = sorted(entry["samples"])
        runs: list[list[float]] = []  # [start, end, saw_firing]
        for ts in stamps:
            if runs and ts - runs[-1][1] <= ALERT_GAP_STEPS * step_s:
                runs[-1][1] = ts
                runs[-1][2] = runs[-1][2] or entry["samples"][ts] == "firing"
            else:
                runs.append([ts, ts, entry["samples"][ts] == "firing"])
        fired = [r for r in runs if r[2]]
        if not fired:
            continue  # only ever pending: not an alert yet
        latest = fired[-1]
        active = entry["samples"][latest[1]] == "firing" and now_ts - latest[1] <= 2 * step_s
        signal = alert_signal(
            name,
            entry["kept"],
            entry["severity"],
            onset=_utc(latest[0]),
            first_seen=_utc(fired[0][0]),
            last_seen=_utc(latest[1]),
            occurrences=len(fired),
            active=active,
        )
        if signal is not None:
            out.append(signal)
    return out


# ------------------------------------------------------------------------------------------------------- anomalies
def live_query(name: str) -> str:
    """The training query for a signal, with the per-run `job` filter opened up to every job (the live platform's
    services each have their own)."""
    return SIGNALS[name][2].replace('job="{job}"', 'job=~".+"').format(w=WINDOW)


def fetch_signal_matrix(
    start: datetime, end: datetime, url: str = PROM_URL
) -> tuple[list[datetime], np.ndarray]:
    """The detector's eight signals on a `STEP_S` grid from `start` to `end`; NaN where Prometheus had no value."""
    first = int(start.timestamp()) // STEP_S * STEP_S
    last = int(end.timestamp()) // STEP_S * STEP_S
    grid = list(range(first, last + 1, STEP_S))
    index = {ts: i for i, ts in enumerate(grid)}
    matrix = np.full((len(grid), len(SIGNAL_NAMES)), np.nan)
    for j, name in enumerate(SIGNAL_NAMES):
        for series in _range(live_query(name), _utc(first), _utc(last), STEP_S, url)[:1]:
            for ts, value in series["values"]:
                i = index.get(int(round(ts)))
                if i is not None:
                    matrix[i, j] = float(value)
    return [_utc(ts) for ts in grid], matrix


@dataclass
class AnomalyTimeline:
    times: list[datetime]
    scores: np.ndarray  # NaN before the first step with enough history
    alarms: np.ndarray  # bool: the alarm rule at each step
    above: np.ndarray  # bool: score >= threshold at each step
    deviations: np.ndarray  # steps x signals


def build_anomaly_timeline(
    detector: OperationsDetector, times: list[datetime], matrix: np.ndarray
) -> AnomalyTimeline:
    from models.operations_detector.evaluate import alarms as alarm_rule

    scores = np.full(len(times), np.nan)
    if len(times) >= HISTORY:
        scores[HISTORY - 1 :] = detector.score_all(matrix)
    above = np.nan_to_num(scores, nan=-np.inf) >= detector.threshold
    alarm = alarm_rule(np.nan_to_num(scores, nan=-np.inf), detector.threshold)
    return AnomalyTimeline(times, scores, alarm, above, detector.deviations(matrix))


def anomaly_signals(timeline: AnomalyTimeline, now: datetime) -> list[Signal]:
    """The anomaly signals as they stand at `now`; the timeline is causal, so a step's alarm never depends on later data."""
    last = max((i for i, t in enumerate(timeline.times) if t <= now), default=-1)
    if last < 0:
        return []
    fresh = (
        now - timeline.times[last]
    ).total_seconds() <= 2 * STEP_S  # no newer data than this: nothing is 'active'
    found: dict[str, list[tuple[int, int, float]]] = {}
    for first, end in episodes(timeline.alarms[: last + 1]):
        onset = max(0, first - (PERSISTENCE - 1))
        peak = onset + int(
            np.nanargmax(np.nan_to_num(timeline.scores[onset : end + 1], nan=-np.inf))
        )
        deviation = timeline.deviations[peak]
        order = [j for j in np.argsort(-deviation) if deviation[j] >= ATTRIBUTION_DEVIATION][
            :MAX_ATTRIBUTED
        ]
        names = [SIGNAL_NAMES[j] for j in order] or ["unattributed"]
        for name in names:
            value = float(deviation[SIGNAL_NAMES.index(name)]) if name != "unattributed" else 0.0
            found.setdefault(name, []).append((onset, end, value))
    out = []
    for name, eps in found.items():
        latest = eps[-1]
        out.append(
            anomaly_signal(
                name,
                deviation=max(e[2] for e in eps),
                onset=timeline.times[latest[0]],
                first_seen=timeline.times[eps[0][0]],
                last_seen=timeline.times[latest[1]],
                occurrences=len(eps),
                active=latest[1] == last and fresh,
            )
        )
    return out


def collect_live(
    detector: OperationsDetector | None, now: datetime | None = None, lookback_s: int = 900
):
    """One live reading: the alert signals and (when a detector is given) the anomaly signals, both as of `now`."""
    now = now or datetime.now(UTC)
    start = now - timedelta(seconds=lookback_s)
    signals = alert_signals(fetch_alert_series(start, now), now)
    if detector is not None:
        times, matrix = fetch_signal_matrix(start, now)
        signals += anomaly_signals(build_anomaly_timeline(detector, times, matrix), now)
    return signals
