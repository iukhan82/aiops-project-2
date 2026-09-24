import { useEffect, useMemo, useState } from "react";
import { api, type Page } from "../../api/client";
import type { CommandRecord, EmergencyCall, Incident } from "../../api/types";
import { useAction } from "../../api/useAction";
import { useApi } from "../../api/useApi";
import { useAuth } from "../../auth/AuthContext";
import { ConfirmDialog, type ConfirmResult } from "../../components/Dialog";
import { LayerToggle, Select, TextField } from "../../components/controls";
import { utcDateTime, words } from "../../lib/format";
import { ACTION_WORDS } from "../actions/model";
import type { Handover } from "./model";

export const SHIFTS = ["Day", "Evening", "Night"] as const;

interface Candidate {
  key: string;
  kind: "incident" | "command" | "call";
  ref: string;
  label: string;
}

const OPEN_INCIDENT = new Set(["open", "acknowledged", "investigating", "escalated", "reopened"]);
const OPEN_COMMAND = new Set(["requested", "approved", "executing"]);
const OPEN_CALL = new Set(["received", "dispatched", "unit_assigned", "en_route", "on_scene"]);

/**
 * Writing a handover. What is still open is offered from the live records - the outgoing person ticks what the next shift must pick
 * up - and the server says what each tick is when it stores it, so the note cannot claim something that is not in the system.
 */
export function WriteHandoverDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const { can } = useAuth();
  const incidents = useApi<Page<Incident>>(open && can("incidents.view") ? "/api/v1/incidents" : null, { limit: 100 });
  const commands = useApi<Page<CommandRecord>>(open && can("commands.view") ? "/api/v1/commands" : null, { limit: 100 });
  const calls = useApi<Page<EmergencyCall>>(open && can("emergency.view") ? "/api/v1/emergency/calls" : null, { limit: 100 });
  const save = useAction();
  const [outgoing, setOutgoing] = useState<string>("Day");
  const [incoming, setIncoming] = useState<string>("Night");
  const [summary, setSummary] = useState("");
  const [extra, setExtra] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<{ summary?: string; shifts?: string }>({});
  const [result, setResult] = useState<ConfirmResult | null>(null);

  useEffect(() => {
    if (open) {
      setSummary("");
      setExtra("");
      setPicked(new Set());
      setErrors({});
      setResult(null);
    }
  }, [open]);

  const candidates = useMemo<Candidate[]>(
    () => [
      ...(incidents.data?.items ?? []).filter((i) => OPEN_INCIDENT.has(i.status)).map((i) => ({ key: `incident:${i.incident_id}`, kind: "incident" as const, ref: i.incident_id, label: `Incident: ${words(i.incident_type).toLowerCase()} on ${i.network_element_id} (${i.status}, ${i.severity})` })),
      ...(commands.data?.items ?? []).filter((c) => OPEN_COMMAND.has(c.status)).map((c) => ({ key: `command:${c.command_id}`, kind: "command" as const, ref: c.command_id, label: `Command: ${(ACTION_WORDS[c.action_type] ?? words(c.action_type)).toLowerCase()} on ${c.target.entity_id} (${c.status})` })),
      ...(calls.data?.items ?? []).filter((c) => OPEN_CALL.has(c.status)).map((c) => ({ key: `call:${c.call_id}`, kind: "call" as const, ref: c.call_id, label: `Call: ${c.call_type}, ${c.priority} priority (${words(c.status).toLowerCase()})` })),
    ],
    [incidents.data, commands.data, calls.data],
  );
  const loading = incidents.loading || commands.loading || calls.loading;
  const chosen = candidates.filter((c) => picked.has(c.key));

  const submit = async () => {
    const found: typeof errors = {};
    if (!summary.trim()) found.summary = "Write what the next shift needs to know.";
    if (outgoing === incoming) found.shifts = "A handover goes from one shift to a different one.";
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    const outcome = await save.run(() =>
      api<Handover>("/api/v1/handovers", {
        method: "POST",
        body: {
          outgoing_shift: outgoing,
          incoming_shift: incoming,
          summary: summary.trim(),
          open_items: [...chosen.map((c) => ({ kind: c.kind, ref: c.ref })), ...(extra.trim() ? [{ kind: "other", note: extra.trim() }] : [])],
        },
      }),
    );
    if (outcome.ok) {
      setResult({ tone: "ok", title: "Handover recorded", message: `It is waiting for someone on the ${incoming} shift to acknowledge it by name. It is under your name and cannot be edited.` });
      onDone();
    } else {
      setResult({ tone: "danger", title: "Not recorded", message: outcome.error.message });
    }
  };

  return (
    <ConfirmDialog
      open={open}
      title="Write a shift handover"
      summary={[
        { label: "From", value: `${outgoing} shift` },
        { label: "To", value: `${incoming} shift` },
        { label: "Open items", value: `${chosen.length + (extra.trim() ? 1 : 0)}` },
      ]}
      statements={["The person who acknowledges it must be someone other than you. Nothing here is executed; a handover is a note."]}
      confirmLabel="Record handover"
      busy={save.busy}
      result={result}
      onConfirm={() => void submit()}
      onClose={onClose}
    >
      <Select label="Outgoing shift" value={outgoing} onChange={setOutgoing} options={SHIFTS.map((s) => ({ value: s, label: `${s} shift` }))} />
      <Select label="Incoming shift" value={incoming} onChange={setIncoming} options={SHIFTS.map((s) => ({ value: s, label: `${s} shift` }))} />
      {errors.shifts ? <p className="error-text">{errors.shifts}</p> : null}
      <TextField label="Summary" value={summary} onChange={setSummary} multiline required error={errors.summary} hint="What happened, what is still going on, what to watch." />
      <fieldset className="open-items">
        <legend>Open items to hand over</legend>
        {loading ? <p className="muted">Loading what is open.</p> : null}
        {!loading && candidates.length === 0 ? <p className="muted">Nothing open that your role can see.</p> : null}
        {candidates.map((c) => (
          <LayerToggle
            key={c.key}
            label={c.label}
            checked={picked.has(c.key)}
            onChange={(on) =>
              setPicked((current) => {
                const next = new Set(current);
                if (on) next.add(c.key);
                else next.delete(c.key);
                return next;
              })
            }
          />
        ))}
      </fieldset>
      <TextField label="Anything else" value={extra} onChange={setExtra} hint="Something that is not an incident, command or call. At most 300 characters." />
    </ConfirmDialog>
  );
}

/** Acknowledging is a person taking the handover over, by name. It restates whose it is and what it hands over. */
export function AcknowledgeDialog({ handover, onClose, onDone }: { handover: Handover | null; onClose: () => void; onDone: () => void }) {
  const { state } = useAuth();
  const me = state.status === "authenticated" ? state.me : null;
  const save = useAction();
  const [result, setResult] = useState<ConfirmResult | null>(null);

  useEffect(() => setResult(null), [handover?.handover_id]);

  const confirm = async () => {
    if (!handover) return;
    const outcome = await save.run(() => api<Handover>(`/api/v1/handovers/${handover.handover_id}/acknowledge`, { method: "POST" }));
    if (outcome.ok) {
      setResult({ tone: "ok", title: "Acknowledged", message: `Recorded under ${outcome.value.acknowledged_by}. The handover is now yours to act on.` });
      onDone();
    } else {
      const who = typeof outcome.error.detail.acknowledged_by === "string" ? outcome.error.detail.acknowledged_by : null;
      setResult({ tone: outcome.error.code === "already_acknowledged" ? "warn" : "danger", title: outcome.error.code === "already_acknowledged" ? "Already acknowledged" : "Not acknowledged", message: who ? `${who} acknowledged it first. Nothing was changed.` : outcome.error.message });
      if (outcome.error.code === "already_acknowledged") onDone();
    }
  };

  return (
    <ConfirmDialog
      open={handover !== null}
      title="Acknowledge this handover"
      summary={
        handover
          ? [
              { label: "From", value: `${handover.outgoing_shift} shift, written by ${handover.author} (${handover.author_roles.map(words).join(", ")})` },
              { label: "To", value: `${handover.incoming_shift} shift` },
              { label: "Written", value: utcDateTime(handover.created_at) },
              { label: "Open items", value: `${handover.open_items.length}` },
              { label: "You are", value: me ? `${me.name || me.username} (${me.username})` : "not signed in" },
            ]
          : []
      }
      statements={["Acknowledging says you have read the summary and taken over its open items. It is recorded under your name and cannot be undone."]}
      confirmLabel="Acknowledge"
      busy={save.busy}
      result={result}
      onConfirm={() => void confirm()}
      onClose={onClose}
    />
  );
}
