import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, type Page as ApiPage } from "../api/client";
import type { CallDetail, EmergencyCall, Observation, RouteResult, Topology } from "../api/types";
import { useApi } from "../api/useApi";
import { useAuth } from "../auth/AuthContext";
import { Banner } from "../components/Banner";
import { usePolledFeed } from "../components/Feed";
import { KeyValueList } from "../components/KeyValueList";
import { Page, Panel } from "../components/Page";
import { RouteCard } from "../components/RouteCard";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { FreshnessBadge, StatusChip, TruthBadge } from "../components/badges";
import { Select } from "../components/controls";
import { Stepper } from "../components/lifecycle";
import { assignmentSteps, callSteps, label } from "../features/dispatch/lifecycle";
import { number, utcClock, words } from "../lib/format";

const CLOSED = new Set(["cleared", "cancelled"]);
const RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

function nearest(topology: Topology | null, lat: number, lon: number): string | null {
  if (!topology || topology.intersections.length === 0) return null;
  const d = (i: { latitude: number; longitude: number }) => (i.latitude - lat) ** 2 + ((i.longitude - lon) * Math.cos((lat * Math.PI) / 180)) ** 2;
  return topology.intersections.reduce((best, i) => (d(i) < d(best) ? i : best)).intersection_id;
}

/** Read-only, one column, large targets: the call, the route and its ETA, and what changed. Nothing here requests, approves or executes anything. */
export function FieldPage() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const list = useApi<ApiPage<EmergencyCall>>("/api/v1/emergency/calls", { limit: 100 }, 10_000);
  const topology = useApi<Topology>("/api/v1/network/topology");
  const active = useMemo(
    () => (list.data?.items ?? []).filter((c) => !CLOSED.has(c.status)).sort((a, b) => (RANK[a.priority] ?? 9) - (RANK[b.priority] ?? 9) || b.reported_at.localeCompare(a.reported_at)),
    [list.data],
  );
  const wanted = params.get("call");
  const call = active.find((c) => c.call_id === wanted) ?? active[0];
  const detail = useApi<CallDetail>(call ? `/api/v1/emergency/calls/${call.call_id}` : null, undefined, 5_000);
  const unit = detail.data?.assignments.find((a) => a.status !== "clear" && a.status !== "unavailable") ?? detail.data?.assignments[0];
  const position = useApi<ApiPage<Observation>>(unit ? "/api/v1/observations" : null, { device_id: unit ? `avl-${unit.unit_id}` : undefined, order: "desc", limit: 1 }, 5_000);
  const unitPosition = position.data?.items[0];
  const origin = unitPosition?.latitude != null && unitPosition.longitude != null ? nearest(topology.data, unitPosition.latitude, unitPosition.longitude) : null;
  const destination = detail.data ? nearest(topology.data, detail.data.call.location.latitude, detail.data.call.location.longitude) : null;
  const route = useApi<RouteResult>(can("routes.view") && origin && destination && origin !== destination ? "/api/v1/routes" : null, { origin, destination }, 10_000);
  const [paused] = useState(false);
  const noPause = useCallback(() => undefined, []);
  usePolledFeed([list, detail], detail.data?.transitions.at(-1)?.changed_at ?? list.data?.items[0]?.reported_at ?? null, paused, noPause);

  const changes = useMemo(() => {
    if (!detail.data) return [];
    return [
      ...detail.data.transitions.map((t) => ({ at: t.changed_at, text: `Call ${label("call", t.to_status).toLowerCase()}${t.note ? `: ${t.note}` : ""}` })),
      ...detail.data.assignment_transitions.map((t) => ({ at: t.changed_at, text: t.from_status === t.to_status ? `${t.unit_id}: ${t.note ?? "updated"}` : `${t.unit_id} ${label("assignment", t.to_status).toLowerCase()}` })),
    ]
      .sort((a, b) => b.at.localeCompare(a.at))
      .slice(0, 6);
  }, [detail.data]);

  const segments = new Map((topology.data?.segments ?? []).map((s) => [s.edge_id, s]));
  const steps = (route.data?.route_alternatives[0]?.edges ?? []).map((edge) => segments.get(edge)).filter((s): s is NonNullable<typeof s> => Boolean(s));
  const offline = list.failures >= 3 || detail.failures >= 3;

  if (list.loading) {
    return (
      <Page title="Field view">
        <LoadingState what="your assignments" />
      </Page>
    );
  }
  if (list.error && !list.data) {
    return (
      <Page title="Field view">
        <ErrorState what="Your assignments" error={list.error} onRetry={list.reload} />
      </Page>
    );
  }
  const lastGood = detail.fetchedAt ?? list.fetchedAt;

  return (
    <Page title="Field view" subtitle="Read only: the call, the route and what changed.">
      {offline ? (
        <Banner tone="danger" alert>
          Offline. Last update {lastGood ? utcClock(new Date(lastGood).toISOString()) : "never"}. What you see is not live.
        </Banner>
      ) : null}
      {active.length === 0 ? (
        <EmptyState title="No active call">Nothing is assigned right now. A new call appears here as soon as it is dispatched.</EmptyState>
      ) : (
        <>
          {active.length > 1 ? (
            <Select
              label="Call"
              value={call?.call_id ?? ""}
              onChange={(value) => setParams({ call: value }, { replace: true })}
              options={active.map((c) => ({ value: c.call_id, label: `${words(c.call_type)}: ${words(c.call_subtype)} (${c.priority})` }))}
            />
          ) : null}
          {call ? (
            <Panel title={`${words(call.call_type)}: ${words(call.call_subtype)}`} id="field-call">
              <KeyValueList
                items={[
                  { label: "Priority", value: <StatusChip domain="severity" value={call.priority} /> },
                  { label: "Status", value: <StatusChip domain="call" value={detail.data?.call.status ?? call.status} /> },
                  { label: "Reported", value: utcClock(call.reported_at), extra: <TruthBadge label={call.truth_label} /> },
                ]}
              />
              {unit ? <Stepper label={`${unit.unit_id} progress`} steps={assignmentSteps(unit.status)} /> : <Stepper label="Call progress" steps={callSteps(detail.data?.call.status ?? call.status)} />}
            </Panel>
          ) : null}

          <Panel title="Route" id="field-route">
            {!unit ? (
              <p className="muted">No unit is assigned to this call yet, so there is no route.</p>
            ) : (
              <>
                <p>
                  {unit.unit_id}
                  {unitPosition ? (
                    <>
                      {" "}
                      last reported <FreshnessBadge observedAt={unitPosition.observation_time} staleAfterSeconds={60} />
                    </>
                  ) : null}
                </p>
                <ul className="route-list" aria-label="Route chosen for this unit">
                  {unit.route_alternatives
                    .filter((r) => r.selected)
                    .map((r) => (
                      <RouteCard key={r.route_id} route={r} index={unit.route_alternatives.indexOf(r)} canSelect={false} />
                    ))}
                </ul>
                <h3>Turn by turn</h3>
                {steps.length === 0 ? (
                  <p className="muted">{route.error instanceof ApiError ? "No route can be worked out right now." : "The unit is at the scene junction, or its position is not known, so there are no steps to show."}</p>
                ) : (
                  <ol className="plain-list" aria-label="Route steps">
                    {steps.map((s) => (
                      <li key={s.edge_id}>
                        From {s.from_node} to {s.to_node}, {number(s.length_m, 0, "m")}
                      </li>
                    ))}
                  </ol>
                )}
                <p className="muted">These steps are worked out just now from the last known position and the closures on the network. They can differ from the route chosen at assignment.</p>
              </>
            )}
          </Panel>

          <Panel title="What changed" id="field-changes">
            {changes.length === 0 ? (
              <p className="muted">Nothing has changed yet.</p>
            ) : (
              <ul className="plain-list">
                {changes.map((c, index) => (
                  <li key={index}>
                    <time dateTime={c.at}>{utcClock(c.at)}</time> {c.text}
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </>
      )}
    </Page>
  );
}
