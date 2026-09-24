import { useCallback, useMemo, useState } from "react";
import type { Page as ApiPage } from "../api/client";
import type { Device, Observation } from "../api/types";
import { useApi } from "../api/useApi";
import { Banner } from "../components/Banner";
import { useNow } from "../components/Clock";
import { DataTable, type Column } from "../components/DataTable";
import { usePolledFeed } from "../components/Feed";
import { KeyValueList } from "../components/KeyValueList";
import { Page, Panel } from "../components/Page";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { FreshnessBadge, StatusChip, TruthBadge, freshnessOf } from "../components/badges";
import { Select } from "../components/controls";
import { DEVICE_STALE_AFTER_S } from "../features/map/model";
import { ageSeconds, number, utcClock, utcDateTime, words } from "../lib/format";

const REFRESH_MS = 15_000;
const CERTIFICATE_WARNING_DAYS = 30;

function summary(measurements: Observation["measurements"]): string {
  return measurements.map((m) => `${words(m.name)} ${typeof m.value === "number" ? number(m.value, Number.isInteger(m.value) ? 0 : 2) : String(m.value)}${m.unit && m.unit !== "count" && m.unit !== "category" && m.unit !== "boolean" ? ` ${m.unit}` : ""}`).join(", ");
}

export function DeviceHealthPage() {
  const now = useNow();
  const [paused, setPaused] = useState(false);
  const [typeFilter, setTypeFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [freshnessFilter, setFreshnessFilter] = useState("all");
  const [selected, setSelected] = useState<string | null>(null);

  const devices = useApi<ApiPage<Device>>("/api/v1/devices", { limit: 500 }, paused ? undefined : REFRESH_MS);
  const detail = useApi<Device>(selected ? `/api/v1/devices/${encodeURIComponent(selected)}` : null, undefined, paused ? undefined : REFRESH_MS);
  const observations = useApi<ApiPage<Observation>>(selected ? "/api/v1/observations" : null, { device_id: selected, order: "desc", limit: 10 }, paused ? undefined : REFRESH_MS);

  const togglePause = useCallback(() => setPaused((p) => !p), []);
  const rows = useMemo(
    () =>
      (devices.data?.items ?? []).map((device) => ({
        device,
        freshness: freshnessOf(device.last_observation_time, DEVICE_STALE_AFTER_S[device.device_type] ?? 300, now),
      })),
    [devices.data, now],
  );
  const newest = useMemo(() => rows.reduce<string | null>((latest, r) => (r.device.last_observation_time && (!latest || r.device.last_observation_time > latest) ? r.device.last_observation_time : latest), null), [rows]);
  usePolledFeed([devices], newest, paused, togglePause);

  const types = useMemo(() => [...new Set(rows.map((r) => r.device.device_type))].sort(), [rows]);
  const statuses = useMemo(() => [...new Set(rows.map((r) => r.device.status))].sort(), [rows]);
  const shown = rows.filter((r) => (typeFilter === "all" || r.device.device_type === typeFilter) && (statusFilter === "all" || r.device.status === statusFilter) && (freshnessFilter === "all" || (freshnessFilter === "never" ? r.device.last_observation_time === null : r.freshness === freshnessFilter && r.device.last_observation_time !== null)));
  const counts = {
    fresh: rows.filter((r) => r.freshness === "fresh").length,
    stale: rows.filter((r) => r.freshness === "stale").length,
    never: rows.filter((r) => r.device.last_observation_time === null).length,
  };

  type Row = (typeof rows)[number];
  const columns: Column<Row>[] = [
    {
      key: "device",
      header: "Device",
      sortValue: (r) => r.device.device_id,
      render: (r) => (
        <button type="button" className="link-button" aria-pressed={selected === r.device.device_id} onClick={() => setSelected(r.device.device_id)}>
          {r.device.device_id}
        </button>
      ),
    },
    { key: "type", header: "Type", sortValue: (r) => r.device.device_type, render: (r) => words(r.device.device_type) },
    { key: "status", header: "Status", sortValue: (r) => r.device.status, render: (r) => <StatusChip domain="device" value={r.device.status} /> },
    {
      key: "freshness",
      header: "Reporting",
      sortValue: (r) => (r.device.last_observation_time ? ageSeconds(r.device.last_observation_time, now) : Number.MAX_SAFE_INTEGER),
      render: (r) => <FreshnessBadge observedAt={r.device.last_observation_time} staleAfterSeconds={DEVICE_STALE_AFTER_S[r.device.device_type] ?? 300} />,
    },
    { key: "truth", header: "Truth label", sortValue: (r) => r.device.deployment_type, render: (r) => <TruthBadge label={r.device.deployment_type === "simulated" ? "simulated" : "measured"} /> },
    { key: "location", header: "Location", sortValue: (r) => r.device.intersection_id ?? r.device.lane_id ?? r.device.corridor_id ?? "", render: (r) => r.device.intersection_id ?? r.device.lane_id ?? r.device.corridor_id ?? "not placed" },
    { key: "certificate", header: "Certificate valid until", sortValue: (r) => r.device.certificate_not_after ?? "", render: (r) => (r.device.certificate_not_after ? utcDateTime(r.device.certificate_not_after) : "not recorded") },
  ];

  const observationColumns: Column<Observation>[] = [
    { key: "time", header: "Observed", render: (o) => utcClock(o.observation_time) },
    { key: "type", header: "Event", render: (o) => o.event_type },
    { key: "values", header: "Values", render: (o) => summary(o.measurements) },
    { key: "truth", header: "Truth label", render: (o) => <TruthBadge label={o.truth_label} /> },
  ];

  if (devices.loading) {
    return (
      <Page title="Device health">
        <LoadingState what="devices" />
      </Page>
    );
  }
  if (devices.error && !devices.data) {
    return (
      <Page title="Device health">
        <ErrorState what="The device registry" error={devices.error} onRetry={devices.reload} />
      </Page>
    );
  }

  const certificate = detail.data?.certificate_not_after ? Date.parse(detail.data.certificate_not_after) : null;
  const certificateDays = certificate === null ? null : Math.floor((certificate - now) / 86_400_000);

  return (
    <Page title="Device health" subtitle="Which devices are reporting, which have gone quiet, and which have never reported.">
      {devices.error ? <Banner tone="warn">The registry could not be refreshed. The table shows the last values received.</Banner> : null}
      <div className="stat-row" role="group" aria-label="Reporting summary">
        <div className="stat"><span className="stat-number">{rows.length}</span><span>devices registered</span></div>
        <div className="stat"><span className="stat-number">{counts.fresh}</span><span>reporting</span></div>
        <div className="stat"><span className="stat-number">{counts.stale}</span><span>stale</span></div>
        <div className="stat"><span className="stat-number">{counts.never}</span><span>never reported</span></div>
      </div>
      <div className="filters">
        <Select label="Device type" value={typeFilter} onChange={setTypeFilter} options={[{ value: "all", label: "All types" }, ...types.map((t) => ({ value: t, label: words(t) }))]} />
        <Select label="Status" value={statusFilter} onChange={setStatusFilter} options={[{ value: "all", label: "All statuses" }, ...statuses.map((t) => ({ value: t, label: words(t) }))]} />
        <Select
          label="Reporting"
          value={freshnessFilter}
          onChange={setFreshnessFilter}
          options={[
            { value: "all", label: "All" },
            { value: "fresh", label: "Reporting" },
            { value: "stale", label: "Stale" },
            { value: "never", label: "Never reported" },
          ]}
        />
      </div>
      <p role="status" className="muted">
        {shown.length} {shown.length === 1 ? "device" : "devices"} shown.
      </p>
      <div className="two-col">
        <Panel title="Devices" id="devices-panel">
          <DataTable
            caption="Every registered device with its status, how recently it reported and its certificate"
            columns={columns}
            rows={shown}
            rowKey={(r) => r.device.device_id}
            selectedKey={selected}
            defaultSort={{ key: "freshness", direction: "desc" }}
            empty={<EmptyState title="No device matches">Change a filter to see more devices.</EmptyState>}
          />
        </Panel>
        <Panel title="Selected device" id="device-detail">
          {!selected ? (
            <p className="muted">Select a device to see its registration and recent observations.</p>
          ) : detail.loading ? (
            <LoadingState what="the device" />
          ) : detail.error && !detail.data ? (
            <ErrorState what="The device" error={detail.error} onRetry={detail.reload} />
          ) : detail.data ? (
            <>
              {certificateDays !== null && certificateDays < 0 ? <Banner tone="danger" alert>The certificate expired {number(-certificateDays, 0)} days ago. The device can no longer authenticate.</Banner> : null}
              {certificateDays !== null && certificateDays >= 0 && certificateDays <= CERTIFICATE_WARNING_DAYS ? <Banner tone="warn">The certificate expires in {number(certificateDays, 0)} days.</Banner> : null}
              <KeyValueList
                items={[
                  { label: "Device", value: detail.data.device_id },
                  { label: "Type", value: words(detail.data.device_type) },
                  { label: "Status", value: <StatusChip domain="device" value={detail.data.status} /> },
                  { label: "Agency", value: detail.data.agency_scope },
                  { label: "Registered", value: utcDateTime(detail.data.registered_at) },
                  { label: "Last observation", value: utcDateTime(detail.data.last_observation_time), extra: <FreshnessBadge observedAt={detail.data.last_observation_time} staleAfterSeconds={DEVICE_STALE_AFTER_S[detail.data.device_type] ?? 300} /> },
                  { label: "Certificate valid until", value: detail.data.certificate_not_after ? utcDateTime(detail.data.certificate_not_after) : "not recorded" },
                ]}
              />
              <h3>Recent observations</h3>
              <DataTable
                caption={`Latest observations from ${detail.data.device_id}`}
                columns={observationColumns}
                rows={observations.data?.items ?? []}
                rowKey={(o) => o.event_id}
                empty={<EmptyState title="No observations">This device has not reported anything yet.</EmptyState>}
              />
            </>
          ) : null}
        </Panel>
      </div>
    </Page>
  );
}
