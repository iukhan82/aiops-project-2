import { useCallback, useState } from "react";
import { useApi } from "../api/useApi";
import { Banner } from "../components/Banner";
import { Button } from "../components/Button";
import { DataTable, type Column } from "../components/DataTable";
import { usePolledFeed } from "../components/Feed";
import { KeyValueList } from "../components/KeyValueList";
import { Page, Panel } from "../components/Page";
import { ErrorState, LoadingState } from "../components/StateViews";
import { FreshnessBadge, StatusChip } from "../components/badges";
import { ageWords, overallSentence, type OpsStatus, type ServiceCheck, type SourceFreshness } from "../features/govern/model";
import { number, utcClock, words } from "../lib/format";

const REFRESH_MS = 10_000;

export function OperationsPage() {
  const [paused, setPaused] = useState(false);
  const status = useApi<OpsStatus>("/api/v1/ops/status", undefined, paused ? undefined : REFRESH_MS);
  const togglePause = useCallback(() => setPaused((p) => !p), []);
  usePolledFeed([status], status.data?.generated_at ?? null, paused, togglePause);

  const serviceColumns: Column<ServiceCheck>[] = [
    { key: "what", header: "Component", sortValue: (s) => s.label, render: (s) => s.label },
    { key: "status", header: "State", sortValue: (s) => s.status, render: (s) => <StatusChip domain="service" value={s.status} /> },
    { key: "detail", header: "What was checked", render: (s) => s.detail },
    { key: "checked", header: "Checked", render: (s) => utcClock(s.checked_at) },
    { key: "latency", header: "Answered in", numeric: true, render: (s) => (s.latency_ms === null ? <span className="muted">not measured</span> : number(s.latency_ms, 0, "ms")) },
  ];
  const sourceColumns: Column<SourceFreshness>[] = [
    { key: "source", header: "Data source", sortValue: (s) => s.label, render: (s) => words(s.label) },
    {
      key: "newest",
      header: "Newest reading",
      render: (s) => <FreshnessBadge observedAt={s.newest_observation_time} staleAfterSeconds={s.budget_s ?? Number.MAX_SAFE_INTEGER} status={s.status} />,
    },
    { key: "budget", header: "Stale after", numeric: true, render: (s) => (s.budget_s === null ? <span className="muted">not applicable</span> : ageWords(s.budget_s)) },
    { key: "devices", header: "Devices", numeric: true, render: (s) => (s.devices === undefined ? <span className="muted">not applicable</span> : s.devices) },
    { key: "reporting", header: "Reporting", numeric: true, render: (s) => (s.reporting === undefined ? <span className="muted">not applicable</span> : s.reporting) },
    { key: "stale", header: "Stale", numeric: true, render: (s) => (s.stale === undefined ? <span className="muted">not applicable</span> : s.stale) },
    { key: "never", header: "Never reported", numeric: true, render: (s) => (s.never === undefined ? <span className="muted">not applicable</span> : s.never) },
  ];

  if (status.loading) {
    return (
      <Page title="Platform status">
        <LoadingState what="the platform status" />
      </Page>
    );
  }
  if (!status.data) {
    return (
      <Page title="Platform status">
        <ErrorState what="The platform status" error={status.error} onRetry={status.reload} />
      </Page>
    );
  }

  const data = status.data;
  const verdict = overallSentence(data);
  const { ingestion } = data;
  const rejected = Object.entries(ingestion.rejected);

  return (
    <Page
      title="Platform status"
      subtitle="Checked when this page asks, not remembered. A check with no answer says unknown and is never shown as healthy."
      actions={
        <Button variant="secondary" onClick={status.reload}>
          Check now
        </Button>
      }
    >
      {status.error ? (
        <Banner tone="warn">The status could not be refreshed. What is shown was checked {utcClock(data.generated_at)}.</Banner>
      ) : null}
      <Banner tone={verdict.tone}>{verdict.text}</Banner>
      <p className="muted">
        Checked {utcClock(data.generated_at)}. This page checks again every {REFRESH_MS / 1000} seconds{paused ? ", but updates are paused" : ""}.
      </p>

      <Panel title="Services and dependencies" id="ops-services">
        <DataTable caption="Services and dependencies" columns={serviceColumns} rows={data.services} rowKey={(s) => s.id} />
        <p className="muted">
          A worker reports a heartbeat while it runs. A broker is checked by opening a connection to it, which shows the port is open, not that messages are flowing; the ingestion figures below show that.
        </p>
      </Panel>

      <Panel title="Data freshness by source" id="ops-sources">
        <DataTable caption="Data freshness by source" columns={sourceColumns} rows={[...data.sources, ...data.analytics]} rowKey={(s) => s.id} empty={<p className="muted">No device is registered, so there is no source to check.</p>} />
      </Panel>

      <Panel title="Ingestion" id="ops-ingestion" actions={<StatusChip domain="service" value={ingestion.status} />}>
        <p>{ingestion.detail}.</p>
        <KeyValueList
          items={[
            { label: `Events stored in the last ${ingestion.window_minutes} minutes`, value: number(ingestion.events) },
            { label: "Median time from observation to storage", value: ingestion.lag_p50_s === null ? "not measured" : number(ingestion.lag_p50_s, 2, "s") },
            { label: "95th percentile", value: ingestion.lag_p95_s === null ? "not measured" : number(ingestion.lag_p95_s, 2, "s") },
            { label: "Newest event stored", value: ingestion.newest_received_at ? `${utcClock(ingestion.newest_received_at)} (${ageWords(ingestion.since_newest_s)} ago)` : "none in this window" },
            { label: "Events refused", value: rejected.length === 0 ? "none in this window" : rejected.map(([reason, count]) => `${words(reason).toLowerCase()} ${count}`).join("; ") },
          ]}
        />
      </Panel>
    </Page>
  );
}
