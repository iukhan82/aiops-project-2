import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { Page as ApiPage } from "../api/client";
import type { Device, NetworkStateList, Observation, Topology } from "../api/types";
import { useApi } from "../api/useApi";
import { Banner } from "../components/Banner";
import { DataTable, type Column } from "../components/DataTable";
import { usePolledFeed } from "../components/Feed";
import { KeyValueList } from "../components/KeyValueList";
import { Page, Panel } from "../components/Page";
import { Shape } from "../components/Shape";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { FreshnessBadge, StatusChip, TruthBadge } from "../components/badges";
import { Select, Tabs } from "../components/controls";
import { DEVICE_STALE_AFTER_S } from "../features/map/model";
import { number, utcClock, utcDateTime, words } from "../lib/format";
import type { ShapeName, Tone } from "../lib/status";

const REFRESH_MS = 5_000;
type TabId = "signal" | "movements" | "devices" | "observations";

/** SUMO/SPaT phase characters, in words. A signal group is never shown by colour alone. */
const SIGNAL: Record<string, { label: string; shape: ShapeName; tone: Tone }> = {
  G: { label: "green", shape: "circle", tone: "ok" },
  g: { label: "green (yield)", shape: "ring", tone: "ok" },
  y: { label: "yellow", shape: "triangle", tone: "warn" },
  Y: { label: "yellow", shape: "triangle", tone: "warn" },
  r: { label: "red", shape: "square", tone: "danger" },
  u: { label: "red and yellow", shape: "diamond", tone: "warn" },
  o: { label: "off, flashing", shape: "pause", tone: "neutral" },
  O: { label: "off", shape: "dash", tone: "neutral" },
};

function measurement(observation: Observation | undefined, name: string): string | number | boolean | null {
  const m = observation?.measurements.find((x) => x.name === name);
  return m ? m.value : null;
}

const latestOf = (items: readonly Observation[], eventType: string): Observation | undefined => items.find((o) => o.event_type === eventType);

export function IntersectionAnalyticsPage() {
  const [params, setParams] = useSearchParams();
  const [paused, setPaused] = useState(false);
  const [tab, setTab] = useState<TabId>("signal");

  const topology = useApi<Topology>("/api/v1/network/topology");
  const intersections = useMemo(() => (topology.data?.intersections ?? []).map((i) => i.intersection_id).sort(), [topology.data]);
  const selected = params.get("intersection") && intersections.includes(params.get("intersection")!) ? params.get("intersection")! : (intersections[0] ?? "");

  const refresh = paused ? undefined : REFRESH_MS;
  const states = useApi<NetworkStateList>("/api/v1/network-state", { element_type: "intersection" }, refresh);
  const stateRecord = states.data?.items.find((s) => s.network_element_id === selected);
  const newestSample = states.data?.newest_sample_time[selected] ?? null;
  const devices = useApi<ApiPage<Device>>("/api/v1/devices", { limit: 500 }, paused ? undefined : 30_000);
  const here = useMemo(() => (devices.data?.items ?? []).filter((d) => d.intersection_id === selected), [devices.data, selected]);
  const controller = here.find((d) => d.device_type === "signal_controller");
  const crossings = here.filter((d) => d.device_type === "crossing_detector");

  const spat = useApi<ApiPage<Observation>>(controller ? "/api/v1/observations" : null, { device_id: controller?.device_id, order: "desc", limit: 30 }, refresh);
  const crossingObservations = useApi<ApiPage<Observation>>(crossings[0] ? "/api/v1/observations" : null, { device_id: crossings[0]?.device_id, order: "desc", limit: 30 }, refresh);
  const togglePause = useCallback(() => setPaused((p) => !p), []);
  const newestSignal = spat.data?.items[0]?.observation_time ?? null;
  usePolledFeed([devices], newestSignal, paused, togglePause);

  const latestSpat = latestOf(spat.data?.items ?? [], "signal.controller.spat");
  const phase = String(measurement(latestSpat, "signal_state") ?? "");
  const activePhase = measurement(latestSpat, "active_phase");
  const noObservations = Boolean(states.data && !stateRecord);

  const deviceColumns: Column<Device>[] = [
    { key: "id", header: "Device", sortValue: (d) => d.device_id, render: (d) => d.device_id },
    { key: "type", header: "Type", sortValue: (d) => d.device_type, render: (d) => words(d.device_type) },
    { key: "status", header: "Status", render: (d) => <StatusChip domain="device" value={d.status} /> },
    {
      key: "reporting",
      header: "Reporting",
      render: (d) => <FreshnessBadge observedAt={d.last_observation_time} staleAfterSeconds={DEVICE_STALE_AFTER_S[d.device_type] ?? 300} />,
      sortValue: (d) => d.last_observation_time ?? "",
    },
    { key: "truth", header: "Truth label", render: (d) => <TruthBadge label={d.deployment_type === "simulated" ? "simulated" : "measured"} /> },
  ];

  const observationColumns: Column<Observation>[] = [
    { key: "time", header: "Observed", sortValue: (o) => o.observation_time, render: (o) => utcClock(o.observation_time) },
    { key: "device", header: "Device", render: (o) => o.device_id },
    { key: "event", header: "Event", render: (o) => o.event_type },
    { key: "values", header: "Values", render: (o) => o.measurements.map((m) => `${words(m.name)} ${typeof m.value === "number" ? number(m.value, Number.isInteger(m.value) ? 0 : 2) : String(m.value)}`).join(", ") },
    { key: "truth", header: "Truth label", render: (o) => <TruthBadge label={o.truth_label} /> },
  ];

  if (topology.loading) {
    return (
      <Page title="Intersection analytics">
        <LoadingState what="intersections" />
      </Page>
    );
  }
  if (!topology.data) {
    return (
      <Page title="Intersection analytics">
        <ErrorState what="The intersection list" error={topology.error} onRetry={topology.reload} />
      </Page>
    );
  }

  const demand = latestOf(crossingObservations.data?.items ?? [], "vru.crossing_detector.demand");
  const clearance = latestOf(crossingObservations.data?.items ?? [], "vru.crossing_detector.clearance");
  const allObservations = [...(spat.data?.items ?? []), ...(crossingObservations.data?.items ?? [])].sort((a, b) => b.observation_time.localeCompare(a.observation_time));

  return (
    <Page title="Intersection analytics" subtitle="Signal state, pedestrian movements and the devices at one junction.">
      <div className="filters">
        <Select
          label="Intersection"
          value={selected}
          onChange={(value) => {
            const copy = new URLSearchParams(params);
            copy.set("intersection", value);
            setParams(copy, { replace: true });
          }}
          options={intersections.map((i) => ({ value: i, label: i }))}
        />
      </div>
      {states.error && states.data ? <Banner tone="warn">The junction state could not be refreshed. The values shown are the last received.</Banner> : null}
      <Tabs
        label="Intersection views"
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "signal", label: "Signal" },
          { value: "movements", label: "Movements" },
          { value: "devices", label: `Devices (${here.length})` },
          { value: "observations", label: "Observations" },
        ]}
      >
        {tab === "signal" ? (
          !controller ? (
            <EmptyState title="No signal controller registered">{selected} has no signal controller in the device registry, so there is no signal state to show.</EmptyState>
          ) : spat.loading ? (
            <LoadingState what="signal state" />
          ) : !latestSpat ? (
            <EmptyState title="The signal controller has not reported">
              {controller.device_id} is registered but has not sent a phase and timing message. Nothing is shown rather than a guessed phase.
            </EmptyState>
          ) : (
            <>
              <KeyValueList
                items={[
                  { label: "Controller", value: controller.device_id },
                  { label: "Active phase", value: activePhase === null ? "not available" : String(activePhase) },
                  { label: "Observed", value: utcDateTime(latestSpat.observation_time), extra: <FreshnessBadge observedAt={latestSpat.observation_time} staleAfterSeconds={DEVICE_STALE_AFTER_S.signal_controller ?? 120} /> },
                  { label: "Truth label", value: <TruthBadge label={latestSpat.truth_label} /> },
                ]}
              />
              <h3>Signal groups</h3>
              <ol className="signal-groups" aria-label="Signal groups, in order">
                {[...phase].map((char, index) => {
                  const look = SIGNAL[char] ?? { label: `unknown (${char})`, shape: "ring" as ShapeName, tone: "neutral" as Tone };
                  return (
                    <li key={index} className={`chip tone-${look.tone}`}>
                      <Shape name={look.shape} />
                      <span>
                        Group {index + 1}: {look.label}
                      </span>
                    </li>
                  );
                })}
              </ol>
              <p className="muted">
                Raw state <code>{phase}</code>. G green, g green yielding, y yellow, r red, u red and yellow, o or O off.
              </p>
            </>
          )
        ) : null}
        {tab === "movements" ? (
          crossings.length === 0 ? (
            <EmptyState title="No pedestrian or cycle detector here">{selected} has no crossing detector registered.</EmptyState>
          ) : (
            <>
              <KeyValueList
                items={[
                  { label: "Crossing detectors", value: crossings.map((c) => c.device_id).join(", ") },
                  { label: "Pedestrian demand", value: demand ? (measurement(demand, "crossing_demand") === true ? "waiting to cross" : "none waiting") : "no reading yet", extra: demand ? <FreshnessBadge observedAt={demand.observation_time} staleAfterSeconds={300} /> : undefined },
                  { label: "Crossing clearance", value: clearance ? (measurement(clearance, "crossing_clear") === true ? "clear" : measurement(clearance, "crossing_clear") === false ? "not clear" : "reported") : "no reading yet" },
                  { label: "Truth label", value: <TruthBadge label={demand?.truth_label ?? "simulated"} /> },
                ]}
              />
              <p className="muted">Demand and clearance come from the crossing detector; they are readings, not a prediction.</p>
            </>
          )
        ) : null}
        {tab === "devices" ? (
          <DataTable
            caption={`Devices registered at ${selected}`}
            columns={deviceColumns}
            rows={here}
            rowKey={(d) => d.device_id}
            empty={<EmptyState title="No device registered here">Devices are placed at an intersection or on a lane. None is registered at {selected}.</EmptyState>}
          />
        ) : null}
        {tab === "observations" ? (
          <DataTable
            caption={`Most recent observations from the devices at ${selected}`}
            columns={observationColumns}
            rows={allObservations}
            rowKey={(o) => o.event_id}
            defaultSort={{ key: "time", direction: "desc" }}
            empty={<EmptyState title="No observations yet">The devices here have not reported.</EmptyState>}
          />
        ) : null}
      </Tabs>
      <Panel title="Junction state" id="junction-state">
        {states.loading ? (
          <LoadingState what="junction state" />
        ) : states.error && !states.data ? (
          <ErrorState what="The junction state" error={states.error} onRetry={states.reload} />
        ) : noObservations ? (
          <p className="muted">No detector has ever reported an observation tagged to {selected}, so there is no aggregated junction state. This is not the same as zero traffic.</p>
        ) : stateRecord ? (
          <KeyValueList
            items={[
              { label: "Window", value: `${number(stateRecord.window_seconds, 0, "s")} average`, extra: <FreshnessBadge observedAt={newestSample} staleAfterSeconds={stateRecord.max_staleness_seconds} status={stateRecord.freshness_status} /> },
              ...stateRecord.measurements.map((m) => ({
                label: words(m.name),
                value: `${typeof m.value === "number" ? number(m.value, Number.isInteger(m.value) ? 0 : 2) : String(m.value)}${m.unit && m.unit !== "category" ? ` ${m.unit}` : ""}`,
                extra: <TruthBadge label={stateRecord.truth_label} />,
              })),
            ]}
          />
        ) : null}
      </Panel>
    </Page>
  );
}
