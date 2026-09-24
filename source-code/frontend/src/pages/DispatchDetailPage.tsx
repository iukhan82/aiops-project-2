import { useCallback, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api, type Page as ApiPage } from "../api/client";
import type { Assignment, CallDetail, CommandRecord, Device, Observation, Recommendation, RouteResult, Topology } from "../api/types";
import { useAction } from "../api/useAction";
import { useApi } from "../api/useApi";
import { useAuth } from "../auth/AuthContext";
import { Banner } from "../components/Banner";
import { Button } from "../components/Button";
import { useNow } from "../components/Clock";
import { ConfirmDialog, type ConfirmResult } from "../components/Dialog";
import { usePolledFeed } from "../components/Feed";
import { KeyValueList } from "../components/KeyValueList";
import { Page, Panel } from "../components/Page";
import { RouteCard } from "../components/RouteCard";
import { ErrorState, LoadingState } from "../components/StateViews";
import { FreshnessBadge, StatusChip, TruthBadge } from "../components/badges";
import { Select, TextField } from "../components/controls";
import { Stepper, Timeline, type TimelineEntry } from "../components/lifecycle";
import { ACTION_LABEL, ASSIGNMENT_ALLOWED, assignmentSteps, callSteps, label, unitKind } from "../features/dispatch/lifecycle";
import { MapCanvas } from "../features/map/MapCanvas";
import { buildModel } from "../features/map/model";
import { number, utcDateTime, words } from "../lib/format";

const CLOSED = new Set(["cleared", "cancelled"]);
const distanceM = (a: { lat: number; lon: number }, b: { lat: number; lon: number }) => {
  const p1 = (a.lat * Math.PI) / 180;
  const p2 = (b.lat * Math.PI) / 180;
  const h = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(((b.lon - a.lon) * Math.PI) / 360) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
};

function nearest(topology: Topology | null, lat: number, lon: number): string | null {
  if (!topology || topology.intersections.length === 0) return null;
  return topology.intersections.reduce((best, i) => (distanceM({ lat, lon }, { lat: i.latitude, lon: i.longitude }) < distanceM({ lat, lon }, { lat: best.latitude, lon: best.longitude }) ? i : best)).intersection_id;
}

export function DispatchDetailPage() {
  const { callId = "" } = useParams();
  const { can } = useAuth();
  const now = useNow();
  const canDispatch = can("emergency.dispatch");
  const [paused, setPaused] = useState(false);
  const refresh = paused ? undefined : 5000;
  const detail = useApi<CallDetail>(`/api/v1/emergency/calls/${encodeURIComponent(callId)}`, undefined, refresh);
  const topology = useApi<Topology>("/api/v1/network/topology");
  const units = useApi<ApiPage<Device>>(canDispatch ? "/api/v1/devices" : null, { device_type: "emergency_cad_avl_adapter", limit: 50 });
  const recommendations = useApi<ApiPage<Recommendation>>(can("recommendations.view") ? "/api/v1/recommendations" : null, { trigger_emergency_call_id: callId, limit: 50 }, refresh);
  const commands = useApi<ApiPage<CommandRecord>>(can("commands.view") ? "/api/v1/commands" : null, { limit: 200 }, refresh);
  const [chosenUnit, setChosenUnit] = useState("");
  const [origin, setOrigin] = useState("");
  const [focus, setFocus] = useState<string | null>(null);
  const assign = useAction();
  const advance = useAction();
  const choose = useAction();
  const cancel = useAction();
  const [notice, setNotice] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelNote, setCancelNote] = useState("");
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [cancelResult, setCancelResult] = useState<ConfirmResult | null>(null);

  const togglePause = useCallback(() => setPaused((p) => !p), []);
  usePolledFeed([detail], detail.data?.transitions.at(-1)?.changed_at ?? null, paused, togglePause);

  const data = detail.data;
  const call = data?.call;
  const active = data?.assignments.filter((a) => a.status !== "clear" && a.status !== "unavailable") ?? [];
  const focused: Assignment | undefined = data?.assignments.find((a) => a.assignment_id === focus) ?? active[0] ?? data?.assignments[0];
  const position = useApi<ApiPage<Observation>>(focused ? "/api/v1/observations" : null, { device_id: focused ? `avl-${focused.unit_id}` : undefined, order: "desc", limit: 1 }, refresh);
  const unitObservation = position.data?.items[0];
  const originNode = unitObservation?.latitude != null && unitObservation.longitude != null ? nearest(topology.data, unitObservation.latitude, unitObservation.longitude) : null;
  const destinationNode = call ? nearest(topology.data, call.location.latitude, call.location.longitude) : null;
  const canRoute = can("routes.view") && originNode !== null && destinationNode !== null && originNode !== destinationNode && Boolean(focused) && !CLOSED.has(call?.status ?? "");
  const live = useApi<RouteResult>(canRoute ? "/api/v1/routes" : null, { origin: originNode, destination: destinationNode }, refresh);

  const model = useMemo(() => {
    if (!topology.data || !call) return null;
    return buildModel({ topology: topology.data, states: [], newestSample: new Map(), devices: [], incidents: [], calls: [call], units: unitObservation ? [unitObservation] : [], live: new Map(), now, canOpenIncident: false, canOpenCall: false });
  }, [topology.data, call, unitObservation, now]);

  const timeline = useMemo<TimelineEntry[]>(() => {
    if (!data) return [];
    return [
      ...data.transitions.map((t, i) => ({ id: `c${i}`, at: t.changed_at, source: t.changed_by, text: `Call: ${t.from_status ? `${label("call", t.from_status)} to ${label("call", t.to_status)}` : `created as ${label("call", t.to_status)}`}${t.note ? ` (${t.note})` : ""}` })),
      ...data.assignment_transitions.map((t, i) => ({ id: `a${i}`, at: t.changed_at, source: t.changed_by, text: `${t.unit_id}: ${t.from_status === t.to_status ? t.note ?? "updated" : `${t.from_status ? `${label("assignment", t.from_status)} to ${label("assignment", t.to_status)}` : `created as ${label("assignment", t.to_status)}`}${t.note ? ` (${t.note})` : ""}`}` })),
    ].sort((a, b) => a.at.localeCompare(b.at));
  }, [data]);

  const linked = useMemo(() => {
    const ids = new Set((recommendations.data?.items ?? []).map((r) => r.recommendation_id));
    return (commands.data?.items ?? []).filter((c) => c.recommendation_id && ids.has(c.recommendation_id));
  }, [recommendations.data, commands.data]);

  if (detail.loading) {
    return (
      <Page title="Call, units and routes">
        <LoadingState what="the call" />
      </Page>
    );
  }
  if (!data || !call) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <Page title="Call, units and routes">
        {notFound ? (
          <div role="status" className="state">
            <h2>There is no such call</h2>
            <p>
              <Link to="/dispatch">Back to the call list</Link>
            </p>
          </div>
        ) : (
          <ErrorState what="The call" error={detail.error} onRetry={detail.reload} />
        )}
      </Page>
    );
  }

  const closed = CLOSED.has(call.status);
  const fits = units.data?.items.filter((d) => unitKind(d.device_id.replace(/^avl-/, "")) === call.call_type && !active.some((a) => `avl-${a.unit_id}` === d.device_id)) ?? [];
  const unit = chosenUnit || fits[0]?.device_id.replace(/^avl-/, "") || "";

  const doAssign = async () => {
    setNotice(null);
    const outcome = await assign.run(() => api(`/api/v1/emergency/calls/${call.call_id}/assignments`, { method: "POST", body: { unit_id: unit, origin_intersection_id: origin || undefined } }));
    if (outcome.ok) {
      setNotice(`${unit} assigned. Routes were worked out from ${origin ? origin : "its last reported position"}.`);
      detail.reload();
    }
  };
  const doAdvance = async (assignmentId: string, to: string) => {
    setNotice(null);
    const outcome = await advance.run(() => api(`/api/v1/emergency/assignments/${assignmentId}/transition`, { method: "POST", body: { to_status: to } }));
    if (outcome.ok) {
      setNotice(`Recorded: ${ACTION_LABEL[to] ?? to}.`);
      detail.reload();
    }
  };
  const doChoose = async (assignmentId: string, routeId: string) => {
    setNotice(null);
    const outcome = await choose.run(() => api(`/api/v1/emergency/assignments/${assignmentId}/route`, { method: "POST", body: { route_id: routeId } }));
    if (outcome.ok) {
      setNotice("Route selected. The choice is in the timeline.");
      detail.reload();
    }
  };
  const doCancel = async () => {
    if (!cancelNote.trim()) {
      setCancelError("A reason is required to cancel a call.");
      return;
    }
    setCancelError(null);
    const outcome = await cancel.run(() => api(`/api/v1/emergency/calls/${call.call_id}/transition`, { method: "POST", body: { to_status: "cancelled", note: cancelNote.trim() } }));
    setCancelResult(outcome.ok ? { tone: "ok", title: "Call cancelled", message: "The reason is on the timeline." } : { tone: "danger", title: "Not cancelled", message: outcome.error.message });
    if (outcome.ok) detail.reload();
  };

  const busy = advance.busy || choose.busy;
  const failure = assign.error ?? advance.error ?? choose.error;

  return (
    <Page
      title="Call, units and routes"
      subtitle={
        <>
          {words(call.call_type)}: {words(call.call_subtype)}. <Link to="/dispatch">All calls</Link>
        </>
      }
      actions={
        canDispatch && !closed ? (
          <Button
            variant="secondary"
            onClick={() => {
              setCancelling(true);
              setCancelNote("");
              setCancelError(null);
              setCancelResult(null);
            }}
          >
            Cancel call
          </Button>
        ) : null
      }
    >
      {notice ? <Banner tone="info">{notice}</Banner> : null}
      {failure ? <Banner tone="danger" alert>{failure.message}</Banner> : null}
      {detail.error ? <Banner tone="warn">The call could not be refreshed. What is shown was current at the last refresh.</Banner> : null}

      <div className="two-col">
        <div className="stack">
          <Panel title="Call" id="call-summary">
            <KeyValueList
              items={[
                { label: "Priority", value: <StatusChip domain="severity" value={call.priority} /> },
                { label: "Status", value: <StatusChip domain="call" value={call.status} /> },
                { label: "Reported", value: utcDateTime(call.reported_at) },
                { label: "Source", value: words(call.source_reliability), extra: <TruthBadge label={call.truth_label} /> },
                { label: "Location", value: `${number(call.location.latitude, 5)}, ${number(call.location.longitude, 5)}` },
              ]}
            />
            <Stepper label="Call progress" steps={callSteps(call.status)} />
            <p className="muted">A call follows its units. It is only moved by hand to cancel it.</p>
          </Panel>

          <Panel title="Units and routes" id="units">
            {data.assignments.length === 0 ? <p className="muted">No unit is assigned yet.</p> : null}
            {data.assignments.map((a) => {
              const legal = ASSIGNMENT_ALLOWED[a.status] ?? [];
              return (
                <section key={a.assignment_id} className="unit-block" aria-label={`Unit ${a.unit_id}`} data-assignment-id={a.assignment_id}>
                  <div className="unit-head">
                    <h3>{a.unit_id}</h3>
                    <StatusChip domain="assignment" value={a.status} />
                    <TruthBadge label={a.truth_label} />
                    <Button variant="ghost" onClick={() => setFocus(a.assignment_id)} aria-pressed={focused?.assignment_id === a.assignment_id}>
                      Show on map
                    </Button>
                  </div>
                  <p className="muted">
                    {a.agency}, capability {a.capability.join(", ")}. Assigned {utcDateTime(a.assigned_at)}.
                  </p>
                  <Stepper label={`${a.unit_id} progress`} steps={assignmentSteps(a.status)} />
                  {canDispatch && legal.length > 0 ? (
                    <div className="button-row" role="group" aria-label={`Move ${a.unit_id}`}>
                      {legal.map((to) => (
                        <Button key={to} variant={to === "unavailable" ? "secondary" : "primary"} busy={busy} onClick={() => void doAdvance(a.assignment_id, to)}>
                          {ACTION_LABEL[to] ?? to}
                        </Button>
                      ))}
                    </div>
                  ) : null}
                  <h4>Route alternatives</h4>
                  <ul className="route-list" aria-label={`Route alternatives for ${a.unit_id}`}>
                    {a.route_alternatives.map((r, index) => (
                      <RouteCard key={r.route_id} route={r} index={index} canSelect={canDispatch && a.status !== "clear" && a.status !== "unavailable"} busy={choose.busy} onSelect={() => void doChoose(a.assignment_id, r.route_id)} />
                    ))}
                  </ul>
                </section>
              );
            })}
            {canDispatch && !closed ? (
              <div className="unit-block" role="group" aria-label="Assign a unit">
                <h3>Assign a unit</h3>
                {fits.length === 0 ? (
                  <p className="muted">Every {call.call_type} unit is already assigned to this call.</p>
                ) : (
                  <>
                    <Select label="Unit" value={unit} onChange={setChosenUnit} options={fits.map((d) => ({ value: d.device_id.replace(/^avl-/, ""), label: d.device_id.replace(/^avl-/, "") }))} />
                    <Select
                      label="Where the unit is"
                      value={origin}
                      onChange={setOrigin}
                      options={[{ value: "", label: "Use its last reported position" }, ...(topology.data?.intersections ?? []).map((i) => ({ value: i.intersection_id, label: i.intersection_id }))]}
                      hint="If a unit has not reported recently the system will not route from an old position: say which junction it is at."
                    />
                    <Button variant="primary" busy={assign.busy} onClick={() => void doAssign()}>
                      Assign unit
                    </Button>
                  </>
                )}
              </div>
            ) : null}
          </Panel>
        </div>

        <div className="stack">
          <Panel title="Map" id="call-map">
            {model ? (
              <MapCanvas model={model} layers={{ traffic: true, devices: false, incidents: false, emergency: true }} selectedId={`call:${call.call_id}`} onSelect={() => undefined} now={now} compact routeEdges={live.data?.route_alternatives[0]?.edges ?? []} />
            ) : (
              <LoadingState what="the map" />
            )}
            <p className="muted">
              {focused && originNode ? (
                live.data ? (
                  <>Blue underlay: the current best route from {focused.unit_id}&apos;s last reported position ({originNode}) to the scene ({destinationNode}), worked out just now. It can differ from the route chosen at assignment.</>
                ) : (
                  <>No live route to draw between {originNode} and {destinationNode}.</>
                )
              ) : (
                <>The map is a preview. The route list is the full information.</>
              )}
            </p>
            {unitObservation ? (
              <p>
                Unit position <FreshnessBadge observedAt={unitObservation.observation_time} staleAfterSeconds={60} />
              </p>
            ) : null}
          </Panel>

          <Panel title="Pre-emption and related commands" id="call-commands">
            {!can("recommendations.view") ? (
              <p className="muted">Recommendations and commands are not part of your role.</p>
            ) : (recommendations.data?.items.length ?? 0) === 0 ? (
              <p className="muted">No recommendation has been raised for this call.</p>
            ) : (
              <ul className="plain-list">
                {recommendations.data?.items.map((r) => (
                  <li key={r.recommendation_id}>
                    <Link to="/actions/recommendations">{words(r.action_type)}</Link> <StatusChip domain="recommendation" value={r.status} />
                  </li>
                ))}
              </ul>
            )}
            {linked.length > 0 ? (
              <ul className="plain-list">
                {linked.map((c) => (
                  <li key={c.command_id}>
                    <Link to={`/actions/commands/${c.command_id}`}>Command: {words(c.action_type)}</Link> <StatusChip domain="command" value={c.status} />
                    {c.approved_by ? <span className="muted"> approved by {c.approved_by}</span> : null}
                  </li>
                ))}
              </ul>
            ) : null}
            <p className="muted">A person requests and another approves; neither executes. Execution is done by the command executor service.</p>
          </Panel>

          <Panel title="Timeline" id="call-timeline">
            <Timeline label="Call and unit timeline" entries={timeline} />
          </Panel>
        </div>
      </div>

      <ConfirmDialog
        open={cancelling}
        title="Cancel this call"
        summary={[
          { label: "Call", value: `${words(call.call_type)}: ${words(call.call_subtype)}` },
          { label: "Now", value: label("call", call.status) },
          { label: "Units", value: active.length > 0 ? `${active.map((a) => a.unit_id).join(", ")} stay assigned until you mark them unavailable or clear` : "none assigned" },
        ]}
        statements={["Cancelling cannot be undone."]}
        confirmLabel="Cancel the call"
        confirmVariant="danger"
        busy={cancel.busy}
        result={cancelResult}
        onConfirm={() => void doCancel()}
        onClose={() => setCancelling(false)}
      >
        <TextField label="Why is it cancelled" value={cancelNote} onChange={setCancelNote} multiline required error={cancelError} />
      </ConfirmDialog>
    </Page>
  );
}
