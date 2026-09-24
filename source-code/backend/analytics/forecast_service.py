"""P06.03: forecast service - stored corridor KPIs in, `forecast/v1` records out.

`forecast_from_kpi_rows` is a pure function (no database), so the abstention
rules and the train/serve equivalence are testable without infrastructure;
`forecast_corridors` is the thin DB wrapper. Features come from
`forecast_data.sample_inputs`, the same helper training used.

It abstains - emits nothing for that series/horizon and says why - rather
than forecast from inputs it should not trust (SAFE-03): stale inputs, a gap
in the last six windows, any `invalid` window, or a target window outside the
09:00-12:00 timeline the model was trained on.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT))

from backend.analytics.forecast_data import (  # noqa: E402
    HORIZONS_S,
    LAGS,
    SERIES_FIELDS,
    TARGETS,
    UNITS,
    WINDOWS_PER_RUN,
    SampleSet,
    sample_inputs,
)
from backend.analytics.forecast_model import MODEL_ID, MODEL_VERSION, ForecastPackage  # noqa: E402
from backend.analytics.kpis import iso_z, parse_time  # noqa: E402

TIMELINE_ANCHOR = parse_time("2026-09-18T09:00:00Z")
WINDOW_S = 300
MAX_INPUT_AGE_S = 2 * WINDOW_S


def _record(key, horizon_s, origin_end, geometry, measurements, model_id, baseline_id):
    valid_from = origin_end + timedelta(seconds=horizon_s - WINDOW_S)
    element = f"{key[0]}/{key[1]}"
    rec = {
        "schema_version": "1.0.0",
        "forecast_id": str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"forecast:{element}:{horizon_s}:{iso_z(valid_from)}:{model_id}:{MODEL_VERSION}",
            )
        ),
        "network_element_type": "corridor",
        "network_element_id": element,
        "geometry_version": geometry,
        "predicted_at": iso_z(origin_end),
        "horizon_seconds": horizon_s,
        "valid_from": iso_z(valid_from),
        "valid_until": iso_z(valid_from + timedelta(seconds=WINDOW_S)),
        "model_id": model_id,
        "model_version": MODEL_VERSION,
        "measurements": measurements,
        "truth_label": "predicted",
    }
    if baseline_id:
        rec["baseline_id"] = baseline_id
    return rec


def forecast_from_kpi_rows(
    rows: list[dict], package: ForecastPackage, origin_end: datetime, as_of: datetime, geometry: str
) -> tuple[list[dict], list[dict]]:
    """`rows`: stored KPI rows (with corridor_id, direction, window_start,
    quality, kpis) covering at least the last six windows before `origin_end`.
    Returns (forecast records, abstentions)."""
    records: list[dict] = []
    abstentions: list[dict] = []
    if (as_of - origin_end).total_seconds() > MAX_INPUT_AGE_S:
        return [], [
            {
                "series": "*",
                "reason": f"stale input: newest window ended {(as_of - origin_end).total_seconds():.0f}s ago",
            }
        ]

    origin_start = origin_end - timedelta(seconds=WINDOW_S)
    t = int((origin_start - TIMELINE_ANCHOR).total_seconds() // WINDOW_S)
    wanted = [
        iso_z(origin_start - timedelta(seconds=WINDOW_S * k)) for k in range(LAGS - 1, -1, -1)
    ]
    by_key: dict[tuple[str, str], dict[str, dict]] = {}
    for r in rows:
        by_key.setdefault((r["corridor_id"], r["direction"]), {})[r["window_start"]] = r

    series: dict[tuple[str, str], np.ndarray] = {}
    for key, windows in sorted(by_key.items()):
        missing = [w for w in wanted if w not in windows]
        if missing:
            abstentions.append(
                {"series": "/".join(key), "reason": f"gap: missing window(s) {missing[:2]}"}
            )
        elif any(windows[w]["quality"] == "invalid" for w in wanted):
            abstentions.append({"series": "/".join(key), "reason": "invalid input window"})
        else:
            arr = np.full((max(t + 1, LAGS), len(SERIES_FIELDS)), np.nan)
            for i, w in enumerate(wanted):
                arr[t - LAGS + 1 + i] = [windows[w]["kpis"][f] for f in SERIES_FIELDS]
            series[key] = arr
    if len(series) != len(by_key):  # network-context features need every series present
        return [], abstentions + [
            {"series": "*", "reason": "network context incomplete: not all corridor series usable"}
        ]
    if t < LAGS - 1:
        return [], abstentions + [
            {"series": "*", "reason": f"window index {t} before the trained range"}
        ]

    for key in sorted(series):
        hist, ctx = sample_inputs(series, key, t)
        for horizon_s, bins in HORIZONS_S.items():
            if t + bins > WINDOWS_PER_RUN - 1:
                abstentions.append(
                    {
                        "series": "/".join(key),
                        "horizon_seconds": horizon_s,
                        "reason": "target window beyond the trained timeline",
                    }
                )
                continue
            s = SampleSet(
                bins,
                [{"corridor_id": key[0], "direction": key[1], "t": t}],
                hist[None],
                np.array([ctx]),
                np.zeros((1, len(TARGETS))),
            )
            by_source: dict[tuple[str, str | None], list[dict]] = {}
            for target in TARGETS:
                out = package.predict(s, horizon_s, target)
                value, half = float(out["value"][0]), float(out["halfwidth"])
                m = {
                    "name": target,
                    "value": round(value, 4),
                    "unit": UNITS[target],
                    "uncertainty": {
                        "method": "quantile_interval",
                        "lower_bound": round(max(0.0, value - half), 4),
                        "upper_bound": round(value + half, 4),
                    },
                }
                if out["source"] == "model":
                    by_source.setdefault(
                        (MODEL_ID, package.entries[(horizon_s, target)].baseline), []
                    ).append(m)
                else:
                    by_source.setdefault(
                        (f"{MODEL_ID}-baseline:{out['source'].split(':', 1)[1]}", None), []
                    ).append(m)
            for (model_id, baseline_id), measurements in by_source.items():
                records.append(
                    _record(
                        key, horizon_s, origin_end, geometry, measurements, model_id, baseline_id
                    )
                )
    return records, abstentions


def fetch_kpi_rows(conn: psycopg.Connection, origin_end: datetime, geometry: str) -> list[dict]:
    start = origin_end - timedelta(seconds=WINDOW_S * LAGS)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT corridor_id, direction, window_start, quality, kpis FROM corridor_kpis "
            "WHERE geometry_version = %s AND window_seconds = %s AND window_start >= %s AND window_start < %s",
            (geometry, WINDOW_S, start, origin_end),
        )
        return [
            {"corridor_id": c, "direction": d, "window_start": iso_z(w), "quality": q, "kpis": k}
            for c, d, w, q, k in cur.fetchall()
        ]


def store_forecasts(conn: psycopg.Connection, records: list[dict]) -> None:
    with conn.cursor() as cur:
        for r in records:
            cur.execute(
                """
                INSERT INTO forecasts (forecast_id, network_element_type, network_element_id, geometry_version, predicted_at,
                    horizon_seconds, valid_from, valid_until, model_id, model_version, baseline_id, measurements)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (forecast_id) DO UPDATE SET predicted_at = EXCLUDED.predicted_at,
                    measurements = EXCLUDED.measurements, baseline_id = EXCLUDED.baseline_id
                """,
                (
                    r["forecast_id"],
                    r["network_element_type"],
                    r["network_element_id"],
                    r["geometry_version"],
                    r["predicted_at"],
                    r["horizon_seconds"],
                    r["valid_from"],
                    r["valid_until"],
                    r["model_id"],
                    r["model_version"],
                    r.get("baseline_id"),
                    Jsonb(r["measurements"]),
                ),
            )
    conn.commit()


def forecast_corridors(
    conn: psycopg.Connection,
    package: ForecastPackage,
    origin_end: datetime,
    as_of: datetime,
    geometry: str,
    store: bool = True,
) -> tuple[list[dict], list[dict]]:
    records, abstentions = forecast_from_kpi_rows(
        fetch_kpi_rows(conn, origin_end, geometry), package, origin_end, as_of, geometry
    )
    if store and records:
        store_forecasts(conn, records)
    return records, abstentions
