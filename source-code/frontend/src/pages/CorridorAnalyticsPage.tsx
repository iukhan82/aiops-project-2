import { useCallback, useMemo, useState } from "react";
import type { Page as ApiPage } from "../api/client";
import type { Candidate, Forecast, NetworkState, Topology } from "../api/types";
import { useApi } from "../api/useApi";
import { Banner } from "../components/Banner";
import { useNow } from "../components/Clock";
import { DataTable, type Column } from "../components/DataTable";
import { usePolledFeed } from "../components/Feed";
import { KeyValueList } from "../components/KeyValueList";
import { LineChart } from "../components/LineChart";
import { Page, Panel } from "../components/Page";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { FreshnessBadge, StatusChip, TruthBadge } from "../components/badges";
import { Select } from "../components/controls";
import { HORIZONS, METRICS, actualSeries, forecastSeries, forecastSources, splitWindows, windowStartMs } from "../features/analytics/corridors";
import { number, utcClock, words } from "../lib/format";

const REFRESH_MS = 15_000;

export function CorridorAnalyticsPage() {
  const now = useNow();
  const [corridor, setCorridor] = useState("corridor-a");
  const [direction, setDirection] = useState("east");
  const [metricKey, setMetricKey] = useState("travel_time_s");
  const [horizon, setHorizon] = useState("900");
  const [paused, setPaused] = useState(false);

  const topology = useApi<Topology>("/api/v1/network/topology");
  const corridors = useMemo(() => [...new Set((topology.data?.segments ?? []).map((s) => s.corridor_id).filter((c): c is string => Boolean(c)))].sort(), [topology.data]);
  const directions = useMemo(() => [...new Set((topology.data?.segments ?? []).filter((s) => s.corridor_id === corridor).map((s) => s.direction))].sort(), [topology.data, corridor]);
  const activeDirection = directions.includes(direction) || directions.length === 0 ? direction : (directions[0] ?? direction);

  const refresh = paused ? undefined : REFRESH_MS;
  const kpis = useApi<ApiPage<NetworkState>>("/api/v1/kpis/corridors", { corridor_id: corridor, direction: activeDirection, limit: 48 }, refresh);
  const forecasts = useApi<ApiPage<Forecast>>("/api/v1/forecasts/corridors", { corridor_id: corridor, direction: activeDirection, limit: 300 }, refresh);
  const candidates = useApi<ApiPage<Candidate>>("/api/v1/candidates", { limit: 100 }, refresh);

  const togglePause = useCallback(() => setPaused((p) => !p), []);
  const metric = METRICS.find((m) => m.key === metricKey) ?? METRICS[0]!;
  const horizonSeconds = Number(horizon);
  const { complete, inProgress } = useMemo(() => splitWindows(kpis.data?.items ?? [], now), [kpis.data, now]);
  const actual = useMemo(() => actualSeries(complete, metric), [complete, metric]);
  const predicted = useMemo(() => forecastSeries(forecasts.data?.items ?? [], metric, horizonSeconds), [forecasts.data, metric, horizonSeconds]);
  const sources = useMemo(() => forecastSources(forecasts.data?.items ?? []), [forecasts.data]);
  const latest = complete[complete.length - 1];
  usePolledFeed([kpis, forecasts, candidates], latest?.observation_time ?? null, paused, togglePause);

  const detections = useMemo(
    () => (candidates.data?.items ?? []).filter((c) => c.attributes.corridor_id === corridor && (!c.attributes.direction || c.attributes.direction === activeDirection)),
    [candidates.data, corridor, activeDirection],
  );

  const forecastReason = !metric.forecast
    ? `${metric.label} is not one of the quantities the forecast model predicts (travel time, volume and density are), so no forecast is drawn.`
    : predicted.points.length === 0
      ? "No forecast is available for this metric and horizon. The forecast service withholds a forecast when its inputs are stale, incomplete or invalid rather than guessing."
      : undefined;

  const columns: Column<Candidate>[] = [
    { key: "kind", header: "Detected", sortValue: (c) => c.kind, render: (c) => words(c.kind) },
    { key: "element", header: "Where", sortValue: (c) => c.network_element_id, render: (c) => c.network_element_id },
    { key: "severity", header: "Severity", sortValue: (c) => c.severity, render: (c) => <StatusChip domain="severity" value={c.severity} /> },
    { key: "onset", header: "Onset", sortValue: (c) => c.onset_time, render: (c) => utcClock(c.onset_time) },
    { key: "clear", header: "Cleared", sortValue: (c) => c.clear_time ?? "", render: (c) => (c.clear_time ? utcClock(c.clear_time) : "still active") },
    { key: "confidence", header: "Detector confidence", numeric: true, sortValue: (c) => c.confidence, render: (c) => number(c.confidence * 100, 0, "%") },
    { key: "truth", header: "Truth label", render: (c) => <TruthBadge label={c.truth_label} /> },
  ];

  if (topology.loading) {
    return (
      <Page title="Corridor analytics">
        <LoadingState what="corridors" />
      </Page>
    );
  }
  if (topology.error && !topology.data) {
    return (
      <Page title="Corridor analytics">
        <ErrorState what="The corridor list" error={topology.error} onRetry={topology.reload} />
      </Page>
    );
  }

  return (
    <Page title="Corridor analytics" subtitle="Measured history, the forecast, and what the forecast is worth, for one corridor direction.">
      {kpis.error && kpis.data ? (
        <Banner tone="warn" action={<button type="button" className="btn btn-secondary" onClick={kpis.reload}>Retry</button>}>
          The KPIs could not be refreshed. The chart shows the last values received.
        </Banner>
      ) : null}
      <div className="filters">
        <Select label="Corridor" value={corridor} onChange={setCorridor} options={corridors.map((c) => ({ value: c, label: c }))} />
        <Select label="Direction" value={activeDirection} onChange={setDirection} options={directions.map((d) => ({ value: d, label: words(d) }))} />
        <Select label="Metric" value={metricKey} onChange={setMetricKey} options={METRICS.map((m) => ({ value: m.key, label: `${m.label} (${m.unit})` }))} />
        <Select label="Forecast horizon" value={horizon} onChange={setHorizon} options={HORIZONS.map((h) => ({ value: h.value, label: h.label }))} hint={metric.forecast ? undefined : "This metric has no forecast."} />
      </div>

      {kpis.loading ? (
        <LoadingState what="KPIs" />
      ) : kpis.error && !kpis.data ? (
        <ErrorState what="The corridor KPIs" error={kpis.error} onRetry={kpis.reload} />
      ) : complete.length === 0 ? (
        <EmptyState title="No completed KPI window yet">KPIs are computed in 5-minute windows from detector counts. The first appears when a full window of readings exists.</EmptyState>
      ) : (
        <div className="grid-2">
          <Panel title="Latest completed window" id="latest-window">
            {latest ? (
              <KeyValueList
                items={[
                  { label: "Window", value: `${utcClock(new Date(windowStartMs(latest)).toISOString())} to ${utcClock(latest.observation_time)}`, extra: <FreshnessBadge observedAt={latest.observation_time} staleAfterSeconds={latest.max_staleness_seconds} /> },
                  ...METRICS.slice(0, 6).map((m) => {
                    const measurement = latest.measurements.find((x) => x.name === m.key);
                    return {
                      label: m.label,
                      value: measurement && typeof measurement.value === "number" ? number(measurement.value * (m.scale ?? 1), m.digits, m.unit) : "not available",
                      extra: measurement ? <TruthBadge label={latest.truth_label} /> : undefined,
                    };
                  }),
                  { label: "Quality", value: latest.measurements[0]?.quality ?? "not available" },
                  { label: "Samples", value: number(latest.measurements[0]?.sample_count ?? null, 0) },
                ]}
              />
            ) : null}
            {inProgress ? <p className="muted">The window ending {utcClock(inProgress.observation_time)} is still filling and is not drawn as an actual value.</p> : null}
          </Panel>
          <Panel title="Forecast source" id="forecast-source">
            {sources.length === 0 ? (
              <p className="muted">No forecast has been produced yet. The service needs six complete windows of history.</p>
            ) : (
              <ul className="plain-list">
                {sources.map((s) => (
                  <li key={s.horizon}>
                    <strong>{number(s.horizon / 60, 0, "min")}</strong>: {s.kind === "trained model" ? "trained model" : "baseline"} <code>{s.name}</code> v{s.version}
                    {s.kind === "baseline" ? <span className="muted"> (the trained model did not beat this baseline on validation for this horizon, so the baseline is served)</span> : null}
                  </li>
                ))}
              </ul>
            )}
            <p className="muted">
              Forecasts are labelled <TruthBadge label="predicted" /> and are never shown as measurements. The band is a nominal 80% interval; measured coverage on held-out runs is reported in the model card.
            </p>
          </Panel>
        </div>
      )}

      {complete.length > 0 ? (
        <Panel title={`${metric.label}: measured and forecast`} id="chart-panel">
          <LineChart
            title={`${metric.label} on ${corridor} ${activeDirection}`}
            unit={metric.unit}
            digits={metric.digits}
            series={[
              { id: "actual", label: "Measured", points: actual, marker: "circle", color: "var(--color-accent)" },
              ...(metric.forecast && predicted.points.length > 0 ? [{ id: "predicted", label: `Forecast ${number(horizonSeconds / 60, 0, "min")} ahead`, points: predicted.points, dash: "8 5", marker: "diamond" as const, color: "var(--color-info)" }] : []),
            ]}
            band={metric.forecast && predicted.band.points.length > 0 ? predicted.band : null}
            nowMs={now}
            gapMs={450_000}
            paused={paused}
            onTogglePause={() => setPaused((p) => !p)}
            note={forecastReason}
          />
        </Panel>
      ) : null}

      <Panel title="Detections on this corridor" id="detections">
        {candidates.error && !candidates.data ? (
          <ErrorState what="Detections" error={candidates.error} onRetry={candidates.reload} />
        ) : (
          <DataTable
            caption={`Detector output for ${corridor} (candidates, not confirmed incidents)`}
            columns={columns}
            rows={detections}
            rowKey={(c) => c.candidate_id}
            defaultSort={{ key: "onset", direction: "desc" }}
            empty={<EmptyState title="No detections">The detectors have raised nothing on this corridor. A candidate becomes an incident only after correlation.</EmptyState>}
          />
        )}
      </Panel>
    </Page>
  );
}
