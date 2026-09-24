"""P06.03: forecast package, drift measures and service abstention on a tiny
synthetic package. The trained model's held-out results, serving/offline
equivalence and API behaviour are proven by
backend/analytics/verify_forecasts.py (docs/evidence/p06_03_forecast_model.json)."""

from datetime import timedelta

import numpy as np
import pytest

from backend.analytics import forecast_service as svc
from backend.analytics.forecast_baselines import TimeOfDayMean
from backend.analytics.forecast_data import (
    HORIZONS_S,
    SERIES_FIELDS,
    TARGETS,
    WINDOWS_PER_RUN,
    RunSeries,
    feature_matrix,
    make_samples,
)
from backend.analytics.forecast_model import (
    Entry,
    ForecastPackage,
    IntegrityError,
    drift_flag,
    fit_candidate,
    load_package,
    psi,
    save_package,
)

KEYS = [(c, d) for c in ("corridor-a", "corridor-b", "corridor-c") for d in ("east", "west")]


def synthetic_run(run_id: str, level: float) -> RunSeries:
    t = np.arange(WINDOWS_PER_RUN, dtype=float)
    series = {
        k: np.stack([level + 5 * t + i, 5 + 0.1 * t, 60 + 0 * t, 14 + 0 * t, 0 * t], axis=1)
        for i, k in enumerate(KEYS)
    }
    return RunSeries(run_id, "train", "am-peak", series, [f"w{i}" for i in range(WINDOWS_PER_RUN)])


def synthetic_package() -> ForecastPackage:
    runs = [synthetic_run("a", 100.0), synthetic_run("b", 140.0)]
    entries = {}
    for horizon_s, bins in HORIZONS_S.items():
        s = make_samples(runs, bins)
        for j, target in enumerate(TARGETS):
            est = fit_candidate(
                "ridge", {"alpha": 1.0}, feature_matrix(s), s.y[:, j] - s.hist[:, -1, j]
            )
            entries[(horizon_s, target)] = Entry(
                horizon_s,
                target,
                "ridge",
                {"alpha": 1.0},
                est,
                10.0,
                target != "travel_time_s",
                "persistence",
                12.0,
                1.0,
                2.0,
            )
    return ForecastPackage(entries, TimeOfDayMean(runs), {"feature_version": "forecast-kpi/1"})


def kpi_rows(t: int) -> list[dict]:
    rows = []
    for w in range(t - 5, t + 1):
        start = svc.TIMELINE_ANCHOR + timedelta(seconds=300 * w)
        for i, (c, d) in enumerate(KEYS):
            vals = [100.0 + 5 * w + i, 5 + 0.1 * w, 60.0, 14.0, 0.0]
            rows.append(
                {
                    "corridor_id": c,
                    "direction": d,
                    "window_start": svc.iso_z(start),
                    "quality": "valid",
                    "kpis": dict(zip(SERIES_FIELDS, vals, strict=True)),
                }
            )
    return rows


def origin(t: int):
    return svc.TIMELINE_ANCHOR + timedelta(seconds=300 * (t + 1))


def test_psi_flags_a_shift_and_ignores_resampling() -> None:
    rng = np.random.default_rng(0)
    base = rng.normal(0, 1, 4000)
    assert psi(base, rng.normal(0, 1, 4000)) < 0.05
    assert psi(base, rng.normal(1.5, 1, 4000)) > 0.25


def test_drift_flag_needs_a_full_window_and_a_sustained_excess() -> None:
    assert not drift_flag([9.0] * 11, 1.0)
    assert drift_flag([9.0] * 12, 1.0)
    assert not drift_flag([1.0] * 12, 1.0)


def test_package_round_trips_and_refuses_tampering(tmp_path) -> None:
    pkg = synthetic_package()
    save_package(pkg, tmp_path, {"limitations": []})
    assert load_package(tmp_path).meta == pkg.meta
    blob = bytearray((tmp_path / "models.joblib").read_bytes())
    blob[len(blob) // 3] ^= 0x01
    (tmp_path / "models.joblib").write_bytes(bytes(blob))
    with pytest.raises(IntegrityError):
        load_package(tmp_path)


def test_service_emits_model_and_baseline_records_and_abstains_on_stale_input() -> None:
    pkg = synthetic_package()
    records, abstentions = svc.forecast_from_kpi_rows(
        kpi_rows(10), pkg, origin(10), origin(10) + timedelta(seconds=30), "g1"
    )
    assert not abstentions and len(records) == 6 * 3 * 2
    assert {r["model_id"] for r in records} == {
        "traffic-forecast",
        "traffic-forecast-baseline:persistence",
    }
    assert all(r["truth_label"] == "predicted" for r in records)
    stale, why = svc.forecast_from_kpi_rows(
        kpi_rows(10), pkg, origin(10), origin(10) + timedelta(hours=1), "g1"
    )
    assert stale == [] and "stale" in why[0]["reason"]


def test_a_missing_history_window_stops_all_forecasting() -> None:
    rows = [
        r
        for r in kpi_rows(10)
        if not (
            r["corridor_id"] == "corridor-b"
            and r["window_start"] == svc.iso_z(svc.TIMELINE_ANCHOR + timedelta(seconds=300 * 8))
        )
    ]
    records, why = svc.forecast_from_kpi_rows(
        rows, synthetic_package(), origin(10), origin(10) + timedelta(seconds=30), "g1"
    )
    assert records == [] and any("gap" in w["reason"] for w in why)


def test_horizons_past_the_trained_timeline_abstain_but_short_ones_are_served() -> None:
    records, why = svc.forecast_from_kpi_rows(
        kpi_rows(33), synthetic_package(), origin(33), origin(33) + timedelta(seconds=30), "g1"
    )
    assert {r["horizon_seconds"] for r in records} == {300}
    assert sum("beyond the trained timeline" in w["reason"] for w in why) == 6 * 2
