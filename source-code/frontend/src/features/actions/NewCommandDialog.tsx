import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { Topology } from "../../api/types";
import { useAction } from "../../api/useAction";
import { useApi } from "../../api/useApi";
import { ConfirmDialog, type ConfirmResult } from "../../components/Dialog";
import { Select, TextField } from "../../components/controls";
import { ACTION_WORDS } from "./model";

type Kind = "variable_message_sign" | "signal_plan_change" | "diversion";

/** A command asked for directly (not from a recommendation). It goes through the same request, four-eyes approval and policy as any other. */
export function NewCommandDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const topology = useApi<Topology>(open ? "/api/v1/network/topology" : null);
  const save = useAction();
  const [kind, setKind] = useState<Kind>("variable_message_sign");
  const [target, setTarget] = useState("");
  const [message, setMessage] = useState("");
  const [deviation, setDeviation] = useState("8");
  const [errors, setErrors] = useState<{ message?: string; deviation?: string }>({});
  const [result, setResult] = useState<ConfirmResult | null>(null);
  const [made, setMade] = useState<string | null>(null);
  const key = useRef("");

  useEffect(() => {
    if (open) {
      key.current = crypto.randomUUID();
      setResult(null);
      setMade(null);
      setErrors({});
      setMessage("");
    }
  }, [open]);

  const segments = (topology.data?.segments ?? []).map((s) => s.edge_id).sort();
  const intersections = (topology.data?.intersections ?? []).map((i) => i.intersection_id).sort();
  const options = (kind === "signal_plan_change" ? intersections : segments).map((v) => ({ value: v, label: v }));
  const chosen = options.some((o) => o.value === target) ? target : (options[0]?.value ?? "");

  const submit = async () => {
    const found: typeof errors = {};
    if (kind === "variable_message_sign" && !message.trim()) found.message = "Write the message the sign should show.";
    if (kind === "variable_message_sign" && message.length > 120) found.message = "A sign message is at most 120 characters.";
    const seconds = Number(deviation);
    if (kind === "signal_plan_change" && (!Number.isFinite(seconds) || seconds <= 0 || seconds > 20)) found.deviation = "Give the seconds of green to move, more than 0 and at most 20.";
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    const outcome = await save.run(() =>
      api<{ command_id: string; safety_class: string }>("/api/v1/commands", {
        method: "POST",
        body: { action_type: kind, target_entity_id: chosen, idempotency_key: key.current, ...(kind === "variable_message_sign" ? { message: message.trim() } : {}), ...(kind === "signal_plan_change" ? { deviation_s: seconds } : {}) },
      }),
    );
    if (outcome.ok) {
      setMade(outcome.value.command_id);
      setResult({ tone: "ok", title: "Command requested", message: `It is waiting for a different person to approve it (${outcome.value.safety_class}). Nothing has been executed.` });
      onDone();
    } else {
      setResult({ tone: "danger", title: "Not requested", message: outcome.error.message });
    }
  };

  return (
    <ConfirmDialog
      open={open}
      title="Request a command"
      summary={[
        { label: "Action", value: ACTION_WORDS[kind] ?? kind },
        { label: "Target", value: chosen || "none chosen" },
        ...(kind === "variable_message_sign" ? [{ label: "Message", value: message.trim() || "not written yet" }] : []),
        ...(kind === "signal_plan_change" ? [{ label: "Green time moved", value: `${deviation} s, at most 20 s` }] : []),
      ]}
      statements={["Requesting is not executing. A different person approves it, the policy checks it again then, and the command executor service runs it."]}
      confirmLabel="Request command"
      busy={save.busy}
      result={result}
      onConfirm={() => void submit()}
      onClose={onClose}
    >
      <Select label="Action" value={kind} onChange={(v) => setKind(v as Kind)} options={[{ value: "variable_message_sign", label: ACTION_WORDS.variable_message_sign! }, { value: "signal_plan_change", label: ACTION_WORDS.signal_plan_change! }, { value: "diversion", label: ACTION_WORDS.diversion! }]} />
      <Select label={kind === "signal_plan_change" ? "Intersection" : "Road segment"} value={chosen} onChange={setTarget} options={options} />
      {kind === "variable_message_sign" ? <TextField label="Message" value={message} onChange={setMessage} required error={errors.message} hint="At most 120 characters." /> : null}
      {kind === "signal_plan_change" ? <TextField label="Seconds of green to move" value={deviation} onChange={setDeviation} type="number" required error={errors.deviation} hint="Borrowed from the cross street. Pedestrian clearance is kept." /> : null}
      {made ? (
        <p role="status">
          <Link to={`/actions/commands/${made}`}>Open the command</Link>
        </p>
      ) : null}
    </ConfirmDialog>
  );
}
