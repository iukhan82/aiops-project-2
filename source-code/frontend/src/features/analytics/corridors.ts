import type { Forecast, Measurement, NetworkState } from "../../api/types";
import type { ChartBand, ChartPoint } from "../../components/LineChart";

export interface MetricDef {
  key: string;
  label: string;
  /** Unit shown to people (the stored unit may differ: speed is stored in m/s and shown in km/h). */
  unit: string;
  digits: number;
  scale?: number;
  /** Only the metrics the forecast package predicts have a forecast. */
  forecast: boolean;
}

export const METRICS: readonly MetricDef[] = [
  { key: "travel_time_s", label: "Travel time", unit: "s", digits: 0, forecast: true },
  { key: "speed_m_s", label: "Mean speed", unit: "km/h", digits: 1, scale: 3.6, forecast: false },
  { key: "volume_veh_h", label: "Volume", unit: "veh/h", digits: 0, forecast: true },
  { key: "density_veh_km", label: "Density", unit: "veh/km", digits: 1, forecast: true },
  { key: "throughput_veh_h", label: "Throughput", unit: "veh/h", digits: 0, forecast: false },
  { key: "delay_s", label: "Delay", unit: "s", digits: 1, forecast: false },
  { key: "buffer_index", label: "Buffer index", unit: "ratio", digits: 2, forecast: false },
  { key: "travel_time_index", label: "Travel time index", unit: "ratio", digits: 2, forecast: false },
  { key: "queue_fraction", label: "Queue share", unit: "ratio", digits: 2, forecast: false },
];

export const HORIZONS = [
  { value: "300", label: "5 minutes" },
  { value: "900", label: "15 minutes" },
  { value: "1800", label: "30 minutes" },
] as const;

const value = (measurements: readonly Measurement[], key: string): number | null => {
  const m = measurements.find((x) => x.name === key);
  return m && typeof m.value === "number" ? m.value : null;
};

export function windowStartMs(record: NetworkState): number {
  return Date.parse(record.observation_time) - record.window_seconds * 1000;
}

/** A KPI window that has ended is complete; one still filling is shown separately and never drawn as an actual value. */
export function splitWindows(kpis: readonly NetworkState[], nowMs: number): { complete: NetworkState[]; inProgress: NetworkState | null } {
  const sorted = [...kpis].sort((a, b) => Date.parse(a.observation_time) - Date.parse(b.observation_time));
  return {
    complete: sorted.filter((k) => Date.parse(k.observation_time) <= nowMs),
    inProgress: sorted.find((k) => Date.parse(k.observation_time) > nowMs) ?? null,
  };
}

export function actualSeries(complete: readonly NetworkState[], metric: MetricDef): ChartPoint[] {
  return complete.map((k) => {
    const v = value(k.measurements, metric.key);
    return { t: windowStartMs(k), v: v === null ? null : v * (metric.scale ?? 1) };
  });
}

export function forecastSeries(forecasts: readonly Forecast[], metric: MetricDef, horizonSeconds: number): { points: ChartPoint[]; band: ChartBand } {
  const latest = new Map<number, Forecast>();
  for (const f of forecasts) {
    if (f.horizon_seconds !== horizonSeconds || !f.measurements.some((m) => m.name === metric.key)) continue;
    const t = Date.parse(f.valid_from);
    const known = latest.get(t);
    if (!known || f.predicted_at > known.predicted_at) latest.set(t, f);
  }
  const scale = metric.scale ?? 1;
  const ordered = [...latest.entries()].sort((a, b) => a[0] - b[0]);
  const points: ChartPoint[] = [];
  const band: ChartBand = { label: "80% prediction interval (nominal)", points: [] };
  for (const [t, f] of ordered) {
    const m = f.measurements.find((x) => x.name === metric.key);
    if (!m || typeof m.value !== "number") continue;
    points.push({ t, v: m.value * scale });
    if (m.uncertainty) band.points.push({ t, lo: m.uncertainty.lower_bound * scale, hi: m.uncertainty.upper_bound * scale });
  }
  return { points, band };
}

export interface ForecastSource {
  horizon: number;
  kind: "trained model" | "baseline";
  name: string;
  version: string;
}

/** Which model actually produced the forecast at each horizon. When the trained model did not earn its place, the baseline is served and said so. */
export function forecastSources(forecasts: readonly Forecast[]): ForecastSource[] {
  const byHorizon = new Map<number, Forecast>();
  for (const f of forecasts) {
    const known = byHorizon.get(f.horizon_seconds);
    if (!known || f.predicted_at > known.predicted_at) byHorizon.set(f.horizon_seconds, f);
  }
  return [...byHorizon.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([horizon, f]) => {
      const baseline = f.model_id.includes("-baseline:");
      return { horizon, kind: baseline ? "baseline" : "trained model", name: baseline ? f.model_id.split(":")[1] ?? f.model_id : f.model_id, version: f.model_version };
    });
}
