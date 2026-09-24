import { describe, expect, it } from "vitest";
import type { Forecast, NetworkState } from "../../api/types";
import { METRICS, actualSeries, forecastSeries, forecastSources, splitWindows } from "./corridors";

const kpi = (endIso: string, speed: number | null): NetworkState => ({
  record_id: endIso,
  network_element_type: "corridor",
  network_element_id: "corridor-a/east",
  geometry_version: "g",
  window_seconds: 300,
  observation_time: endIso,
  ingest_time: endIso,
  measurements: speed === null ? [] : [{ name: "speed_m_s", value: speed, unit: "m_s-1", quality: "valid", confidence: 1 }],
  truth_label: "inferred",
  freshness_status: "fresh",
  max_staleness_seconds: 600,
});

const forecast = (validFrom: string, predictedAt: string, horizon: number, name: string, value: number, model = "traffic-forecast"): Forecast => ({
  forecast_id: `${validFrom}-${predictedAt}-${horizon}-${name}`,
  network_element_type: "corridor",
  network_element_id: "corridor-a/east",
  geometry_version: "g",
  predicted_at: predictedAt,
  horizon_seconds: horizon,
  valid_from: validFrom,
  valid_until: validFrom,
  model_id: model,
  model_version: "1.0.0",
  measurements: [{ name, value, unit: "s", quality: "valid", confidence: 1, uncertainty: { method: "quantile_interval", lower_bound: value - 2, upper_bound: value + 2 } }],
  truth_label: "predicted",
});

describe("corridor series", () => {
  const now = Date.parse("2026-09-21T06:17:00Z");

  it("draws only finished windows as actual values and keeps the filling window apart", () => {
    const { complete, inProgress } = splitWindows([kpi("2026-09-21T06:10:00Z", 12), kpi("2026-09-21T06:15:00Z", 13), kpi("2026-09-21T06:20:00Z", 5)], now);
    expect(complete).toHaveLength(2);
    expect(inProgress?.observation_time).toBe("2026-09-21T06:20:00Z");
  });

  it("places each actual value at the start of its window and converts speed to km/h", () => {
    const speed = METRICS.find((m) => m.key === "speed_m_s")!;
    const points = actualSeries([kpi("2026-09-21T06:15:00Z", 10)], speed);
    expect(points).toEqual([{ t: Date.parse("2026-09-21T06:10:00Z"), v: 36 }]);
  });

  it("a window without the metric is a gap, not a zero", () => {
    const speed = METRICS.find((m) => m.key === "speed_m_s")!;
    expect(actualSeries([kpi("2026-09-21T06:15:00Z", null)], speed)[0]?.v).toBeNull();
  });

  it("keeps the newest forecast for a target window and its interval, for one horizon and metric only", () => {
    const tt = METRICS.find((m) => m.key === "travel_time_s")!;
    const { points, band } = forecastSeries(
      [
        forecast("2026-09-21T06:20:00Z", "2026-09-21T06:05:00Z", 900, "travel_time_s", 60),
        forecast("2026-09-21T06:20:00Z", "2026-09-21T06:10:00Z", 900, "travel_time_s", 62),
        forecast("2026-09-21T06:20:00Z", "2026-09-21T06:10:00Z", 300, "travel_time_s", 99),
        forecast("2026-09-21T06:25:00Z", "2026-09-21T06:10:00Z", 900, "volume_veh_h", 1500),
      ],
      tt,
      900,
    );
    expect(points).toEqual([{ t: Date.parse("2026-09-21T06:20:00Z"), v: 62 }]);
    expect(band.points).toEqual([{ t: Date.parse("2026-09-21T06:20:00Z"), lo: 60, hi: 64 }]);
  });

  it("says which model produced each horizon: the trained model, or the baseline when the model was not accepted", () => {
    const sources = forecastSources([
      forecast("2026-09-21T06:20:00Z", "2026-09-21T06:15:00Z", 300, "travel_time_s", 60, "traffic-forecast"),
      forecast("2026-09-21T06:30:00Z", "2026-09-21T06:15:00Z", 1800, "travel_time_s", 60, "traffic-forecast-baseline:time_of_day_mean"),
    ]);
    expect(sources).toEqual([
      { horizon: 300, kind: "trained model", name: "traffic-forecast", version: "1.0.0" },
      { horizon: 1800, kind: "baseline", name: "time_of_day_mean", version: "1.0.0" },
    ]);
  });
});
