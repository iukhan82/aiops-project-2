import { useCallback, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { Incident, Topology } from "../api/types";
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
import { ErrorState, LoadingState } from "../components/StateViews";
import { StatusChip, TruthBadge } from "../components/badges";
import { Select, TextField } from "../components/controls";
import { Stepper, Timeline, type TimelineEntry } from "../components/lifecycle";
import { DESKS, LABEL, desk, stepsFor } from "../features/incidents/lifecycle";
import { MapCanvas } from "../features/map/MapCanvas";
import { buildModel } from "../features/map/model";
import { segmentIdOf } from "../features/map/geometry";
import { number, utcDateTime, words } from "../lib/format";

interface Detail {
  incident: Incident;
  evidence_sources: string[] | null;
  evidence_cleared_at: string | null;
  candidates: { candidate_id: string; kind: string; network_element_id: string; onset_time: string; clear_time: string | null; detector_confidence: number; relation: string }[];
  hypotheses: { rank: number; hypothesis: string; likelihood: number; supporting_candidate_ids: string[]; generated_at: string }[];
  transitions: { from_status: string | null; to_status: string; changed_by: string; note: string | null; changed_at: string }[];
  notes: { note_id: number; author: string; author_roles: string[]; note: string; created_at: string }[];
  recommendations: { recommendation_id: string; action_type: string; generated_at: string; expires_at: string; status: string }[];
  commands: { command_id: string; recommendation_id: string; action_type: string; status: string; requested_by: string; requested_at: string; approved_by: string | null }[];
}

export function IncidentDetailPage() {
  const { incidentId = "" } = useParams();
  const { can } = useAuth();
  const now = useNow();
  const canManage = can("incidents.manage");
  const [paused, setPaused] = useState(false);
  const detail = useApi<Detail>(`/api/v1/incidents/${encodeURIComponent(incidentId)}`, undefined, paused ? undefined : 10_000);
  const topology = useApi<Topology>("/api/v1/network/topology");
  const move = useAction();
  const own = useAction();
  const annotate = useAction();
  const [notice, setNotice] = useState<{ tone: "info" | "warn" | "danger"; text: string } | null>(null);
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState<string | null>(null);
  const [owner, setOwner] = useState("");
  const [resolving, setResolving] = useState(false);
  const [resolutionNote, setResolutionNote] = useState("");
  const [resolutionError, setResolutionError] = useState<string | null>(null);
  const [result, setResult] = useState<ConfirmResult | null>(null);

  const togglePause = useCallback(() => setPaused((p) => !p), []);
  usePolledFeed([detail], detail.data?.incident.updated_at ?? null, paused, togglePause);

  const incident = detail.data?.incident;
  const model = useMemo(() => {
    if (!topology.data || !incident) return null;
    return buildModel({ topology: topology.data, states: [], newestSample: new Map(), devices: [], incidents: [incident], calls: [], units: [], live: new Map(), now, canOpenIncident: false, canOpenCall: false });
  }, [topology.data, incident, now]);

  const timeline = useMemo<TimelineEntry[]>(() => {
    if (!detail.data) return [];
    const entries: TimelineEntry[] = [];
    for (const [i, t] of detail.data.transitions.entries()) {
      entries.push({ id: `t${i}`, at: t.changed_at, source: t.changed_by, text: `${t.from_status ? LABEL[t.from_status] ?? t.from_status : "Created"} to ${LABEL[t.to_status] ?? t.to_status}${t.note ? `: ${t.note}` : ""}` });
    }
    for (const n of detail.data.notes) entries.push({ id: `n${n.note_id}`, at: n.created_at, source: `${n.author} (${n.author_roles.map(words).join(", ")})`, text: `Note: ${n.note}` });
    for (const c of detail.data.candidates) {
      entries.push({ id: `c${c.candidate_id}`, at: c.onset_time, source: "detector", text: `${words(c.kind)} on ${c.network_element_id} (${c.relation.replace(/_/g, " ")}), detector confidence ${number(c.detector_confidence * 100, 0, "%")}`, truth: "inferred" });
    }
    return entries.sort((a, b) => a.at.localeCompare(b.at));
  }, [detail.data]);

  if (detail.loading) {
    return (
      <Page title="Incident investigation">
        <LoadingState what="the incident" />
      </Page>
    );
  }
  if (!detail.data || !incident) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <Page title="Incident investigation">
        {notFound ? (
          <div role="status" className="state">
            <h2>There is no such incident</h2>
            <p>
              It may have been merged into another. <Link to="/incidents">Back to the incident queue</Link>
            </p>
          </div>
        ) : (
          <ErrorState what="The incident" error={detail.error} onRetry={detail.reload} />
        )}
      </Page>
    );
  }

  const describeRefusal = (error: ApiError): string => {
    if (error.code === "status_changed") return `${error.message} Someone else has moved this incident; the screen now shows the current state.`;
    return error.message;
  };

  const transition = async (to: string, text?: string): Promise<{ ok: true } | { ok: false; error: ApiError }> => {
    setNotice(null);
    const outcome = await move.run(() => api<{ status: string }>(`/api/v1/incidents/${incident.incident_id}/transition`, { method: "POST", body: { to_status: to, note: text || undefined, expected_status: incident.status } }));
    if (outcome.ok) setNotice({ tone: "info", text: `Recorded: the incident is now ${LABEL[outcome.value.status] ?? outcome.value.status}.` });
    if (outcome.ok || (outcome.error.code === "status_changed")) detail.reload();
    return outcome.ok ? { ok: true } : outcome;
  };

  const chooseStep = (to: string) => {
    if (to !== "resolved") {
      void transition(to);
      return;
    }
    setResolving(true);
    setResult(null);
    setResolutionNote("");
    setResolutionError(null);
  };
  const steps = stepsFor(incident.status, canManage, chooseStep, move.busy);
  const moveError = move.error ? describeRefusal(move.error) : null;

  const confirmResolve = async () => {
    if (!resolutionNote.trim()) {
      setResolutionError("A note is required to resolve an incident.");
      return;
    }
    setResolutionError(null);
    const outcome = await transition("resolved", resolutionNote.trim());
    setResult(outcome.ok ? { tone: "ok", title: "Resolved", message: "The incident is resolved and the note is on its timeline." } : { tone: "danger", title: "Not resolved", message: describeRefusal(outcome.error) });
  };

  const addNote = async () => {
    if (!note.trim()) {
      setNoteError("A note cannot be empty.");
      return;
    }
    setNoteError(null);
    const outcome = await annotate.run(() => api(`/api/v1/incidents/${incident.incident_id}/notes`, { method: "POST", body: { note: note.trim() } }));
    if (outcome.ok) {
      setNote("");
      setNotice({ tone: "info", text: "Note added to the timeline." });
      detail.reload();
    }
  };

  const setDesk = async () => {
    const chosen = owner || incident.owner_role || "OPS";
    const outcome = await own.run(() => api(`/api/v1/incidents/${incident.incident_id}/owner`, { method: "POST", body: { owner_role: chosen } }));
    if (outcome.ok) {
      setNotice({ tone: "info", text: `Owner set to ${desk(chosen)}.` });
      detail.reload();
    }
  };

  const segment = segmentIdOf(incident.network_element_type, incident.network_element_id);
  return (
    <Page
      title="Incident investigation"
      subtitle={
        <>
          {words(incident.incident_type)} at {incident.network_element_id}. <Link to="/incidents">All incidents</Link>
        </>
      }
    >
      {notice ? <Banner tone={notice.tone}>{notice.text}</Banner> : null}
      {moveError ? <Banner tone="warn" alert>{moveError}</Banner> : null}
      {detail.error && detail.data ? <Banner tone="warn">The incident could not be refreshed. What is shown was current at the last refresh.</Banner> : null}

      <div className="two-col">
        <div className="stack">
          <Panel title="Summary" id="incident-summary">
            <KeyValueList
              items={[
                { label: "Severity", value: <StatusChip domain="severity" value={incident.severity} /> },
                { label: "Status", value: <StatusChip domain="incident" value={incident.status} /> },
                { label: "Confidence", value: number(incident.confidence * 100, 0, "%"), extra: <TruthBadge label="inferred" /> },
                { label: "Owner", value: desk(incident.owner_role) },
                { label: "Opened", value: utcDateTime(incident.opened_at) },
                { label: "Updated", value: utcDateTime(incident.updated_at) },
                ...(incident.resolved_at ? [{ label: "Resolved", value: utcDateTime(incident.resolved_at) }] : []),
                { label: "Verified cause", value: incident.verified_cause ?? "None. No person has verified a cause." },
                ...(detail.data.evidence_sources ? [{ label: "Independent evidence", value: detail.data.evidence_sources.map(words).join(", ") }] : []),
              ]}
            />
          </Panel>

          <Panel title="Status" id="incident-status">
            <Stepper label="Incident lifecycle" steps={steps} />
            {incident.status === "resolved" && canManage ? (
              <Button variant="secondary" busy={move.busy} onClick={() => void transition("reopened", "Reopened from the incident screen")}>
                Reopen incident
              </Button>
            ) : null}
            {!canManage ? <p className="muted">You can read this incident. Changing it needs the incidents.manage capability.</p> : null}
          </Panel>

          <Panel title="Hypotheses" id="incident-hypotheses">
            {detail.data.hypotheses.length === 0 ? (
              <p className="muted">No hypothesis has been generated yet.</p>
            ) : (
              <ol className="plain-list" aria-label="Ranked hypotheses">
                {detail.data.hypotheses.map((h) => (
                  <li key={h.rank}>
                    <strong>#{h.rank}</strong> {h.hypothesis} <span className="muted">likelihood {number(h.likelihood * 100, 0, "%")}</span> <TruthBadge label="inferred" />
                  </li>
                ))}
              </ol>
            )}
            <p className="muted">These are ranked guesses from the evidence. None of them is the cause until a person verifies it.</p>
          </Panel>

          <Panel title="Timeline" id="incident-timeline">
            <Timeline label="Incident timeline" entries={timeline} />
          </Panel>
        </div>

        <div className="stack">
          <Panel title="Where" id="incident-location">
            {model ? <MapCanvas model={model} layers={{ traffic: true, devices: false, incidents: true, emergency: false }} selectedId={segment ? `segment:${segment}` : null} onSelect={() => undefined} now={now} compact /> : <LoadingState what="the map" />}
            <p className="muted">
              <Link to="/map">Open the live map</Link>
            </p>
          </Panel>

          {canManage ? (
            <Panel title="Actions" id="incident-actions">
              <div className="field">
                <TextField label="Add a note" value={note} onChange={setNote} multiline error={noteError} hint="Notes are part of the record and cannot be edited." />
                <Button variant="secondary" busy={annotate.busy} onClick={() => void addNote()}>
                  Add note
                </Button>
                {annotate.error ? <p className="error-text" role="alert">{annotate.error.message}</p> : null}
              </div>
              <div className="field">
                <Select label="Owner" value={owner || incident.owner_role || "OPS"} onChange={setOwner} options={Object.entries(DESKS).map(([value, label]) => ({ value, label }))} />
                <Button variant="secondary" busy={own.busy} onClick={() => void setDesk()}>
                  Set owner
                </Button>
                {own.error ? <p className="error-text" role="alert">{own.error.message}</p> : null}
              </div>
            </Panel>
          ) : null}

          <Panel title="Recommendations and commands" id="incident-links">
            {detail.data.recommendations.length === 0 ? <p className="muted">No recommendation has been raised for this incident.</p> : null}
            <ul className="plain-list">
              {detail.data.recommendations.map((r) => (
                <li key={r.recommendation_id}>
                  <Link to="/actions/recommendations">{words(r.action_type)}</Link> <StatusChip domain="recommendation" value={r.status} />
                </li>
              ))}
              {detail.data.commands.map((c) => (
                <li key={c.command_id}>
                  <Link to={`/actions/commands/${c.command_id}`}>Command: {words(c.action_type)}</Link> <StatusChip domain="command" value={c.status} />
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>

      <ConfirmDialog
        open={resolving}
        title="Resolve this incident"
        summary={[
          { label: "Incident", value: `${words(incident.incident_type)} at ${incident.network_element_id}` },
          { label: "Now", value: LABEL[incident.status] ?? incident.status },
          { label: "Will become", value: "Resolved" },
        ]}
        statements={["Resolving records who resolved it and why. A resolved incident can be reopened."]}
        confirmLabel="Resolve incident"
        busy={move.busy}
        result={result}
        onConfirm={() => void confirmResolve()}
        onClose={() => setResolving(false)}
      >
        <TextField label="Why is it resolved" value={resolutionNote} onChange={setResolutionNote} multiline required error={resolutionError} />
      </ConfirmDialog>
    </Page>
  );
}
