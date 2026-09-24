import { useCallback, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Page as ApiPage } from "../api/client";
import type { Alternative, Incident, Recommendation, Topology } from "../api/types";
import { useAction } from "../api/useAction";
import { useApi } from "../api/useApi";
import { useCursorList } from "../api/useCursorList";
import { useAuth } from "../auth/AuthContext";
import { Banner } from "../components/Banner";
import { Button } from "../components/Button";
import { useNow } from "../components/Clock";
import { ConfirmDialog, type ConfirmResult } from "../components/Dialog";
import { usePolledFeed } from "../components/Feed";
import { Page, Panel } from "../components/Page";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { StatusChip, TruthBadge } from "../components/badges";
import { Select } from "../components/controls";
import { LoadMore } from "../components/lifecycle";
import { ACTION_WORDS, SAFETY_WORDS, expiresIn, isNoAction, metrics, targetPreview } from "../features/actions/model";
import { number, utcClock, words } from "../lib/format";

const REFRESH_MS = 10_000;

interface Pending {
  rec: Recommendation;
  alternative: Alternative;
}

export function RecommendationsPage() {
  const { can } = useAuth();
  const now = useNow();
  const canRequest = can("commands.request");
  const [paused, setPaused] = useState(false);
  const [statusFilter, setStatusFilter] = useState("live");
  const list = useCursorList<Recommendation>("/api/v1/recommendations", {}, paused ? undefined : REFRESH_MS);
  const incidents = useApi<ApiPage<Incident>>(can("incidents.view") ? "/api/v1/incidents" : null, { limit: 200 });
  const topology = useApi<Topology>("/api/v1/network/topology");
  const request = useAction();
  const [pending, setPending] = useState<Pending | null>(null);
  const [result, setResult] = useState<ConfirmResult | null>(null);
  const [made, setMade] = useState<string | null>(null);
  const key = useRef("");

  const togglePause = useCallback(() => setPaused((p) => !p), []);
  usePolledFeed([list], list.items[0]?.generated_at ?? null, paused, togglePause);

  const shown = useMemo(() => list.items.filter((r) => (statusFilter === "live" ? r.status === "proposed" || r.status === "requested" : statusFilter === "all" || r.status === statusFilter)), [list.items, statusFilter]);
  const incidentOf = (rec: Recommendation) => incidents.data?.items.find((i) => i.incident_id === rec.trigger_incident_id);

  const open = (rec: Recommendation, alternative: Alternative) => {
    key.current = crypto.randomUUID();
    setResult(null);
    setMade(null);
    setPending({ rec, alternative });
  };

  const confirm = async () => {
    if (!pending) return;
    const outcome = await request.run(() =>
      api<{ command_id: string; created: boolean; safety_class: string }>(`/api/v1/recommendations/${pending.rec.recommendation_id}/request`, {
        method: "POST",
        body: { alternative_id: pending.alternative.alternative_id, idempotency_key: key.current },
      }),
    );
    if (outcome.ok) {
      setMade(outcome.value.command_id);
      setResult({
        tone: "ok",
        title: outcome.value.created ? "Command requested" : "Already requested",
        message: `It is now waiting for a different person to approve it (${outcome.value.safety_class}). Nothing has been executed.`,
      });
      list.reload();
    } else {
      setResult({ tone: "danger", title: "Not requested", message: outcome.error.message });
    }
  };

  if (list.loading) {
    return (
      <Page title="Recommendations">
        <LoadingState what="recommendations" />
      </Page>
    );
  }
  if (list.error && list.items.length === 0) {
    return (
      <Page title="Recommendations">
        <ErrorState what="The recommendations" error={list.error} onRetry={list.reload} />
      </Page>
    );
  }

  const target = pending ? targetPreview(pending.rec, incidentOf(pending.rec), topology.data) : null;

  return (
    <Page title="Recommendations" subtitle="What the platform proposes, what it expects to gain and to cost, and the safety bounds it was checked against. A recommendation is never a command.">
      {list.error ? <Banner tone="warn">The list could not be refreshed. What is shown was current at the last refresh.</Banner> : null}
      <div className="filters">
        <Select
          label="Show"
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: "live", label: "Live (proposed or requested)" },
            { value: "all", label: "All" },
            { value: "superseded", label: "Superseded" },
            { value: "expired", label: "Expired" },
          ]}
        />
      </div>
      <p role="status" className="muted">
        {shown.length} {shown.length === 1 ? "recommendation" : "recommendations"} shown.
      </p>
      {shown.length === 0 ? (
        <EmptyState title="No recommendation">Recommendations appear when an active high or critical incident has something safe to propose. Until then there is nothing to act on.</EmptyState>
      ) : (
        shown.map((rec) => {
          const incident = incidentOf(rec);
          const bounds = rec.safety_bounds as { min_pedestrian_clearance_s?: number; max_signal_deviation_s?: number };
          return (
            <Panel key={rec.recommendation_id} title={ACTION_WORDS[rec.action_type] ?? words(rec.action_type)} id={`rec-${rec.recommendation_id}`} className="rec">
              <p className="rec-meta">
                <StatusChip domain="recommendation" value={rec.status} /> <TruthBadge label="predicted" /> Generated {utcClock(rec.generated_at)}, expires {expiresIn(rec.expires_at, now)}.{" "}
                {incident ? (
                  <>
                    For <Link to={`/incidents/${incident.incident_id}`}>{words(incident.incident_type)} at {incident.network_element_id}</Link>.
                  </>
                ) : rec.trigger_incident_id ? (
                  <>For an incident you cannot open from here.</>
                ) : null}
              </p>
              <ul className="alternatives" aria-label="Alternatives">
                {rec.alternatives.map((alternative, index) => (
                  <li key={alternative.alternative_id ?? index} className="alternative">
                    <h4>{alternative.description}</h4>
                    <dl className="kv kv-dense">
                      <div className="kv-row">
                        <dt>Expected benefit</dt>
                        <dd className="kv-value">{metrics(alternative.predicted_benefit).map((m) => `${m.name} ${m.value}`).join("; ") || "none"}</dd>
                      </div>
                      <div className="kv-row">
                        <dt>Expected harm</dt>
                        <dd className="kv-value">{metrics(alternative.predicted_harm).map((m) => `${m.name} ${m.value}`).join("; ") || "none"}</dd>
                      </div>
                      <div className="kv-row">
                        <dt>Confidence</dt>
                        <dd>
                          <span className="kv-value">{number(Number(alternative.confidence ?? 0) * 100, 0, "%")}</span> <TruthBadge label="predicted" />
                        </dd>
                      </div>
                    </dl>
                    {isNoAction(alternative) ? (
                      <p className="muted">Doing nothing is always allowed. Leave the recommendation alone and it expires.</p>
                    ) : canRequest && (rec.status === "proposed" || rec.status === "requested") ? (
                      <Button variant="primary" onClick={() => open(rec, alternative)}>
                        Request this command
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
              <p className="muted">
                Checked against safety bounds: pedestrian clearance at least {number(bounds.min_pedestrian_clearance_s ?? null, 0, "s")}, signal change at most {number(bounds.max_signal_deviation_s ?? null, 0, "s")}. Any option outside them was dropped, not
                trimmed. Constraints: {Array.isArray(rec.constraints) ? rec.constraints.map(words).join(", ") : "none"}.
              </p>
            </Panel>
          );
        })
      )}
      <LoadMore shown={list.items.length} hasMore={list.hasMore} loading={list.loadingMore} onMore={list.loadMore} noun="recommendations loaded" />
      {!canRequest ? <p className="muted">You can read recommendations. Requesting a command needs the commands.request capability.</p> : null}

      <ConfirmDialog
        open={pending !== null}
        title="Request this command"
        summary={
          pending
            ? [
                { label: "Action", value: ACTION_WORDS[pending.rec.action_type] ?? words(pending.rec.action_type) },
                { label: "Safety class", value: pending.rec.action_type === "diversion" || pending.rec.action_type === "signal_plan_change" ? SAFETY_WORDS["SC-1"] + " (raised to SC-2 if a critical incident is active on the target)" : "set by the server" },
                { label: "Target", value: target ? `${target.entity} (${target.adapter})` : "worked out by the server from the incident" },
                { label: "Reason", value: pending.alternative.description ?? "" },
                { label: "Expected benefit", value: metrics(pending.alternative.predicted_benefit).map((m) => `${m.name} ${m.value}`).join("; ") || "none" },
                { label: "Expected harm", value: metrics(pending.alternative.predicted_harm).map((m) => `${m.name} ${m.value}`).join("; ") || "none" },
                { label: "Constraints", value: Array.isArray(pending.rec.constraints) ? pending.rec.constraints.map(words).join(", ") : "none" },
                { label: "Expires", value: `${expiresIn(pending.rec.expires_at, now)}` },
              ]
            : []
        }
        statements={[
          "Requesting is not executing. A different person has to approve it, and the policy checks it again then.",
          "The command executor service executes approved commands; you will not be able to.",
        ]}
        confirmLabel="Request command"
        busy={request.busy}
        result={result}
        onConfirm={() => void confirm()}
        onClose={() => setPending(null)}
      />
      {made ? (
        <p className="muted" role="status">
          Requested command: <Link to={`/actions/commands/${made}`}>open it</Link>.
        </p>
      ) : null}
    </Page>
  );
}
