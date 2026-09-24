"""P06.02/P06.03: forecasting samples built from corridor KPI series.

Target: the corridor KPI the platform itself would compute `h` windows later
(volume, density, travel time), so a forecast is judged against what an
operator will actually see, not against an oracle. Every feature uses windows
`<= t` only; the target is window `t + h`. Runs never mix across splits (a run
id embeds its split and seed), and one run is the unit of resampling in every
confidence interval.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from backend.analytics.kpis import compute_corridor_kpis
from backend.analytics.topology import Segment

FEATURE_VERSION = "forecast-kpi/1"
LAGS = 6
WINDOWS_PER_RUN = 36
SERIES_FIELDS = ("volume_veh_h", "density_veh_km", "travel_time_s", "speed_m_s", "queue_fraction")
TARGETS = SERIES_FIELDS[:3]
UNITS = {"volume_veh_h": "veh_h-1", "density_veh_km": "veh_km-1", "travel_time_s": "s"}
HORIZONS_S = {300: 1, 900: 3, 1800: 6}
CORRIDORS = ("corridor-a", "corridor-b", "corridor-c")
GEOMETRY = "2026-09-18.1"


@dataclass(frozen=True)
class RunSeries:
    run_id: str
    split: str
    run_spec: str
    series: dict[
        tuple[str, str], np.ndarray
    ]  # (corridor, direction) -> (windows, len(SERIES_FIELDS))
    window_starts: list[str]


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def series_from_events(
    events: list[dict], segments: list[Segment], geometry: str = GEOMETRY
) -> tuple[dict, list[str]]:
    rows: dict[tuple[str, str], list[dict]] = {}
    for k in compute_corridor_kpis(events, segments, geometry):
        rows.setdefault((k["corridor_id"], k["direction"]), []).append(k)
    out, starts = {}, []
    for key, items in sorted(rows.items()):
        items.sort(key=lambda r: r["window_start"])
        out[key] = np.array([[r["kpis"][f] for f in SERIES_FIELDS] for r in items], dtype=float)
        starts = [r["window_start"] for r in items]
    return out, starts


def load_split(dataset_root: Path, split: str, segments: list[Segment]) -> list[RunSeries]:
    runs = []
    for run_dir in sorted(p for p in (dataset_root / split).iterdir() if p.is_dir()):
        series, starts = series_from_events(read_events(run_dir / "events.jsonl"), segments)
        spec = run_dir.name.split("-", 3)[3]
        runs.append(RunSeries(run_dir.name, split, spec, series, starts))
    return runs


@dataclass
class SampleSet:
    horizon_bins: int
    meta: list[dict]
    hist: np.ndarray  # (n, LAGS, len(SERIES_FIELDS)), windows t-LAGS+1 .. t
    context: np.ndarray  # (n, 6): progress, network volume, its 3-window change, corridor one-hot(3) -> see CONTEXT_NAMES
    y: np.ndarray  # (n, len(TARGETS)) at window t + h


CONTEXT_NAMES = (
    "run_progress",
    "network_volume_veh_h",
    "network_volume_change_3",
    "is_corridor_a",
    "is_corridor_b",
    "is_east",
)


def sample_inputs(
    series: dict[tuple[str, str], np.ndarray], key: tuple[str, str], t: int
) -> tuple[np.ndarray, list[float]]:
    """(history, context) for one series at window index `t`. The training
    samples and the serving path both call this, so they cannot skew."""
    corridor, direction = key
    volume_now = float(np.mean([a[t, 0] for a in series.values()]))
    volume_3_ago = float(np.mean([a[t - 3, 0] for a in series.values()]))
    context = [
        t / WINDOWS_PER_RUN,
        volume_now,
        volume_now - volume_3_ago,
        float(corridor == "corridor-a"),
        float(corridor == "corridor-b"),
        float(direction == "east"),
    ]
    return series[key][t - LAGS + 1 : t + 1], context


def make_samples(runs: list[RunSeries], horizon_bins: int) -> SampleSet:
    meta, hist, ctx, y = [], [], [], []
    for run in runs:
        n = len(run.window_starts)
        for key, arr in run.series.items():
            for t in range(LAGS - 1, n - horizon_bins):
                h, c = sample_inputs(run.series, key, t)
                hist.append(h)
                ctx.append(c)
                y.append(arr[t + horizon_bins, : len(TARGETS)])
                meta.append(
                    {
                        "run_id": run.run_id,
                        "run_spec": run.run_spec,
                        "split": run.split,
                        "corridor_id": key[0],
                        "direction": key[1],
                        "t": t,
                    }
                )
    return SampleSet(horizon_bins, meta, np.array(hist), np.array(ctx), np.array(y))


def feature_matrix(s: SampleSet) -> np.ndarray:
    h = s.hist
    deltas = np.stack(
        [
            h[:, -1, 0] - h[:, -2, 0],
            h[:, -1, 0] - h[:, -4, 0],
            h[:, -1, 1] - h[:, -2, 1],
            h[:, -1, 1] - h[:, -4, 1],
        ],
        axis=1,
    )
    return np.concatenate([h.reshape(len(h), -1), deltas, s.context], axis=1)


def feature_names() -> list[str]:
    lag = [f"{f}_t-{LAGS - 1 - i}" for i in range(LAGS) for f in SERIES_FIELDS]
    return lag + ["d_volume_1", "d_volume_3", "d_density_1", "d_density_3", *CONTEXT_NAMES]
