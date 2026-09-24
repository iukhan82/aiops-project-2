import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { Page as ApiPage } from "../api/client";
import type { Device, EmergencyCall, Incident, LiveEvent, NetworkStateList, Observation, Topology } from "../api/types";
import { useApi } from "../api/useApi";
import { useAuth } from "../auth/AuthContext";
import { Banner } from "../components/Banner";
import { Button } from "../components/Button";
import { useNow } from "../components/Clock";
import { DataTable, type Column } from "../components/DataTable";
import { useFeedPublisher } from "../components/Feed";
import { LayerToggle, SegmentedControl, Select, TextField } from "../components/controls";
import { LiveRegion } from "../components/LiveRegion";
import { Page } from "../components/Page";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { FreshnessBadge, StatusChip, TruthBadge } from "../components/badges";
import { number, utcClock, words } from "../lib/format";
import { MapCanvas } from "../features/map/MapCanvas";
import { MapLegend } from "../features/map/MapLegend";
import { SelectionPanel } from "../features/map/SelectionPanel";
import { activeIncidents, buildModel, KIND_LABEL, visibleOnMap, type ItemKind, type Layers, type MapItem } from "../features/map/model";
import { useLiveFeed } from "../features/map/useLiveFeed";

const REPLAY_SPAN_MIN = 60;
const SEVERITY_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

function liveLine(event: LiveEvent): string {
  const values = event.measurements.slice(0, 3).map((m) => `${words(m.name)} ${typeof m.value === "number" ? number(m.value, Number.isInteger(m.value) ? 0 : 2) : String(m.value)}`);
  return `${utcClock(event.observation_time)} ${event.device_id}: ${values.join(", ")}`;
}

export function MapPage() {
  const { can } = useAuth();
  const now = useNow();
  const publish = useFeedPublisher();
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "list" ? "list" : "map";
  const wall = params.get("wall") === "1";
  const canIncidents = can("incidents.view");
  const canEmergency = can("emergency.view");

  const [layers, setLayers] = useState<Layers>({ traffic: true, devices: false, incidents: true, emergency: true });
  const [paused, setPaused] = useState(false);
  const [replayAt, setReplayAt] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [live, setLive] = useState<ReadonlyMap<string, LiveEvent>>(new Map());
  const [recent, setRecent] = useState<string[]>([]);
  const [received, setReceived] = useState(0);
  const [kindFilter, setKindFilter] = useState("all");
  const [search, setSearch] = useState("");
  const dirty = useRef(false);

  const isLive = replayAt === null;
  const polling = isLive && !paused;
  const asOf = replayAt === null ? undefined : new Date(replayAt).toISOString();

  const topology = useApi<Topology>("/api/v1/network/topology");
  const states = useApi<NetworkStateList>("/api/v1/network-state", { element_type: "segment", as_of: asOf }, polling ? 5000 : undefined);
  const devices = useApi<ApiPage<Device>>(isLive ? "/api/v1/devices" : null, { limit: 500 }, polling ? 30000 : undefined);
  const incidents = useApi<ApiPage<Incident>>(canIncidents && isLive ? "/api/v1/incidents" : null, { limit: 200 }, polling ? 10000 : undefined);
  const calls = useApi<ApiPage<EmergencyCall>>(canEmergency && isLive ? "/api/v1/emergency/calls" : null, { limit: 100 }, polling ? 10000 : undefined);
  const unitObservations = useApi<ApiPage<Observation>>(isLive ? "/api/v1/observations" : null, { event_type: "emergency.unit_position.avl", order: "desc", limit: 60 }, polling ? 5000 : undefined);

  const onEvents = useCallback((events: LiveEvent[]) => {
    setLive((current) => {
      const next = new Map(current);
      for (const event of events) {
        const known = next.get(event.device_id);
        if (!known || event.observation_time >= known.observation_time) next.set(event.device_id, event);
      }
      return next;
    });
    setRecent((current) => [...events.slice(-5).reverse().map(liveLine), ...current].slice(0, 8));
    setReceived((n) => n + events.length);
    if (events.some((e) => e.event_type === "traffic.loop_detector.count")) dirty.current = true;
  }, []);

  const feed = useLiveFeed({ enabled: isLive, paused, onEvents });

  const reloadStates = states.reload;
  useEffect(() => {
    const id = window.setInterval(() => {
      if (dirty.current && polling) {
        dirty.current = false;
        reloadStates();
      }
    }, 1000);
    return () => window.clearInterval(id);
  }, [polling, reloadStates]);

  useEffect(() => {
    if (!isLive || wall) {
      publish(null);
      return;
    }
    publish({ state: feed.state, newestAt: feed.newestAt, lastConnectedAt: feed.lastConnectedAt, paused, onTogglePause: () => setPaused((p) => !p) });
    return () => publish(null);
  }, [publish, isLive, wall, feed.state, feed.newestAt, feed.lastConnectedAt, paused]);

  const latestUnits = useMemo(() => {
    const byDevice = new Map<string, Observation>();
    for (const o of unitObservations.data?.items ?? []) if (!byDevice.has(o.device_id)) byDevice.set(o.device_id, o);
    return [...byDevice.values()];
  }, [unitObservations.data]);

  const model = useMemo(() => {
    if (!topology.data) return null;
    return buildModel({
      topology: topology.data,
      states: states.data?.items ?? [],
      newestSample: new Map(Object.entries(states.data?.newest_sample_time ?? {})),
      devices: devices.data?.items ?? [],
      incidents: incidents.data?.items ?? [],
      calls: (calls.data?.items ?? []).filter((c) => c.status !== "cleared" && c.status !== "cancelled"),
      units: latestUnits,
      live,
      now,
      canOpenIncident: canIncidents,
      canOpenCall: canEmergency,
    });
  }, [topology.data, states.data, devices.data, incidents.data, calls.data, latestUnits, live, now, canIncidents, canEmergency]);

  const selected = model?.items.find((i) => i.id === selectedId) ?? null;
  const queue = useMemo(
    () =>
      activeIncidents(incidents.data?.items ?? [])
        .filter((i) => i.severity === "critical" || i.severity === "high")
        .sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9) || b.updated_at.localeCompare(a.updated_at)),
    [incidents.data],
  );

  const skipMap = (event: React.MouseEvent) => {
    event.preventDefault();
    document.getElementById("selection-heading")?.focus();
  };

  const setView = (next: "map" | "list") => {
    const copy = new URLSearchParams(params);
    if (next === "list") copy.set("view", "list");
    else copy.delete("view");
    setParams(copy, { replace: true });
  };

  const startReplay = () => setReplayAt(Date.now() - 10 * 60_000);
  const backToLive = () => {
    setReplayAt(null);
    setPaused(false);
  };

  const visibleLayers: Layers = isLive ? layers : { ...layers, devices: false, incidents: false, emergency: false };
  const listRows = useMemo(() => {
    if (!model) return [];
    const term = search.trim().toLowerCase();
    return model.items.filter((item) => {
      if (!isLive && (item.kind === "device" || item.kind === "incident" || item.kind === "call" || item.kind === "unit")) return false;
      if (kindFilter !== "all" && item.kind !== kindFilter) return false;
      return !term || `${item.name} ${item.reading} ${KIND_LABEL[item.kind]}`.toLowerCase().includes(term);
    });
  }, [model, kindFilter, search, isLive]);

  const columns: Column<MapItem>[] = [
    {
      key: "name",
      header: "Element",
      sortValue: (i) => i.name,
      render: (i) => (
        <button type="button" className="link-button" onClick={() => setSelectedId(i.id)} aria-pressed={i.id === selectedId}>
          {i.name}
        </button>
      ),
    },
    { key: "kind", header: "Kind", sortValue: (i) => KIND_LABEL[i.kind], render: (i) => KIND_LABEL[i.kind] },
    { key: "status", header: "Status", sortValue: (i) => i.status?.value ?? "", render: (i) => (i.status ? <StatusChip domain={i.status.domain} value={i.status.value} /> : <span className="muted">not applicable</span>) },
    { key: "reading", header: "Reading", render: (i) => i.reading },
    { key: "freshness", header: "Freshness", sortValue: (i) => i.freshness ?? "", render: (i) => (i.freshness ? <FreshnessBadge observedAt={i.observedAt} status={i.kind === "segment" ? i.freshness : undefined} /> : <span className="muted">not applicable</span>) },
    { key: "truth", header: "Truth label", sortValue: (i) => i.truth ?? "", render: (i) => (i.truth ? <TruthBadge label={i.truth} /> : <span className="muted">not applicable</span>) },
  ];

  const layerCounts = model ? model.items.filter((i) => visibleOnMap(i, visibleLayers)).length : 0;
  const title = wall ? "Live operations map, wall display" : "Live operations map";

  if (topology.loading) {
    return (
      <Page title={title}>
        <LoadingState what="the network" />
      </Page>
    );
  }
  if (!model) {
    return (
      <Page title={title}>
        <ErrorState what="The network topology" error={topology.error} onRetry={topology.reload} />
      </Page>
    );
  }

  const noReadings = states.data && states.data.items.length === 0;
  const statesStale = Boolean(states.error && states.data);
  const totalSegments = model.items.filter((i) => i.kind === "segment").length;

  return (
    <Page
      title={title}
      subtitle={isLive ? "Traffic state, devices, incidents and emergency units on one schematic." : undefined}
      actions={
        wall ? null : isLive ? (
          <Button variant="secondary" onClick={startReplay}>
            Replay recent history
          </Button>
        ) : (
          <Button variant="primary" onClick={backToLive}>
            Return to live
          </Button>
        )
      }
    >
      {!isLive && replayAt !== null ? (
        <Banner tone="warn">
          <strong>Replay, not live.</strong> The traffic layer shows the network as it was at {utcClock(new Date(replayAt).toISOString())}. Devices, incidents and units are not replayed.
          <div className="replay-control">
            <label htmlFor="replay-slider">Replay time</label>
            <input
              id="replay-slider"
              type="range"
              min={Date.now() - REPLAY_SPAN_MIN * 60_000}
              max={Date.now() - 60_000}
              step={60_000}
              value={replayAt}
              onChange={(event) => setReplayAt(Number(event.target.value))}
              aria-valuetext={utcClock(new Date(replayAt).toISOString())}
            />
            <output>{utcClock(new Date(replayAt).toISOString())}</output>
          </div>
        </Banner>
      ) : null}
      {isLive && paused ? (
        <Banner tone="info" action={<Button onClick={() => setPaused(false)}>Resume updates</Button>}>
          Updates are paused. What you see was current when you paused; it is not being refreshed.
        </Banner>
      ) : null}
      {isLive && !paused && feed.state === "reconnecting" ? <Banner tone="warn">Reconnecting to the live feed. Values may be older than shown.</Banner> : null}
      {isLive && !paused && feed.state === "offline" ? <Banner tone="danger" alert>The live feed is offline. Everything on screen is the last data received and is going stale.</Banner> : null}
      {statesStale ? (
        <Banner tone="warn" action={<Button onClick={states.reload}>Retry</Button>}>
          The traffic state could not be refreshed. The segments shown are the last values received and are marked stale.
        </Banner>
      ) : null}
      {noReadings ? (
        <EmptyState title="No traffic readings yet">
          No detector has reported. When the feed starts, segments appear with their speed and occupancy. Until then every segment is drawn as "no speed reading".
        </EmptyState>
      ) : null}

      {wall ? null : (
        <div className="map-toolbar">
          <SegmentedControl
            label="Map or list"
            value={view}
            options={[
              { value: "map", label: "Map" },
              { value: "list", label: "List" },
            ]}
            onChange={setView}
          />
          <fieldset className="layers" disabled={!isLive}>
            <legend>Layers</legend>
            <LayerToggle label="Traffic" checked={layers.traffic} onChange={(traffic) => setLayers({ ...layers, traffic })} />
            <LayerToggle label="Devices" checked={layers.devices} onChange={(devicesOn) => setLayers({ ...layers, devices: devicesOn })} />
            {canIncidents ? <LayerToggle label="Incidents" checked={layers.incidents} onChange={(incidentsOn) => setLayers({ ...layers, incidents: incidentsOn })} /> : null}
            {canEmergency ? <LayerToggle label="Emergency" checked={layers.emergency} onChange={(emergency) => setLayers({ ...layers, emergency })} /> : null}
          </fieldset>
        </div>
      )}

      <div className="map-layout">
        <div className="map-main">
          {view === "map" || wall ? (
            <>
              <a className="skip-inline" href="#selection-heading" onClick={skipMap}>
                Skip past the map
              </a>
              <MapCanvas model={model} layers={visibleLayers} selectedId={selectedId} onSelect={setSelectedId} now={now} notLive={!isLive || paused} large={wall} />
              <p className="muted map-summary" role="status">
                {layerCounts} elements shown, {totalSegments} road segments. {!isLive ? "Replay, not live." : paused ? "Paused." : ""}
              </p>
              <MapLegend layers={visibleLayers} />
            </>
          ) : (
            <section aria-labelledby="list-heading" className="panel">
              <h2 id="list-heading">All elements</h2>
              <div className="filters">
                <Select
                  label="Kind"
                  value={kindFilter}
                  onChange={setKindFilter}
                  options={[{ value: "all", label: "All kinds" }, ...(Object.keys(KIND_LABEL) as ItemKind[]).map((k) => ({ value: k, label: KIND_LABEL[k] }))]}
                />
                <TextField label="Search" type="search" value={search} onChange={setSearch} hint="Matches name, reading and kind." />
              </div>
              <p role="status" className="muted">
                {listRows.length} {listRows.length === 1 ? "element" : "elements"} shown.
              </p>
              <DataTable
                caption="Every element the map shows, with its reading, freshness and truth label"
                columns={columns}
                rows={listRows}
                rowKey={(i) => i.id}
                selectedKey={selectedId}
                empty={<EmptyState title="Nothing matches">Change the kind or clear the search.</EmptyState>}
                defaultSort={{ key: "kind", direction: "asc" }}
              />
            </section>
          )}
        </div>
        <aside className="map-side" aria-label="Selection and incident queue">
          <SelectionPanel item={selected} staleAfterSeconds={states.data?.max_staleness_seconds ?? 120} />
          {canIncidents ? (
            <section className="panel" aria-labelledby="queue-heading" data-testid="incident-queue">
              <h2 id="queue-heading">Critical incident queue</h2>
              {incidents.error && !incidents.data ? <p className="muted">Incidents could not be loaded.</p> : null}
              {queue.length === 0 ? (
                <p className="muted">No critical or high incident is active.</p>
              ) : (
                <ol className="queue">
                  {queue.map((incident) => (
                    <li key={incident.incident_id}>
                      <StatusChip domain="severity" value={incident.severity} /> <StatusChip domain="incident" value={incident.status} />
                      <div>
                        <Link to={`/incidents/${incident.incident_id}`}>
                          {words(incident.incident_type)} at {incident.network_element_id}
                        </Link>
                      </div>
                      <div className="muted">Opened {utcClock(incident.opened_at)}</div>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          ) : null}
          {wall ? null : <LiveRegion label="Recent updates" entries={recent} total={received} paused={paused} onTogglePause={() => setPaused((p) => !p)} />}
        </aside>
      </div>
    </Page>
  );
}
