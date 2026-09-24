import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { CommandDetail } from "../../api/types";
import { useAction } from "../../api/useAction";
import { useApi } from "../../api/useApi";
import { useAuth } from "../../auth/AuthContext";
import { ConfirmBody, Dialog, type ConfirmResult } from "../../components/Dialog";
import { TextField } from "../../components/controls";
import { LoadingState } from "../../components/StateViews";
import { Button } from "../../components/Button";
import { number, utcDateTime, words } from "../../lib/format";
import { ACTION_WORDS, SAFETY_WORDS, expiresIn } from "./model";

interface Basis {
  description?: string;
  predicted_benefit?: { name: string; value: number; unit: string }[];
  predicted_harm?: { name: string; value: number; unit: string }[];
  confidence?: number;
  constraints?: string[];
}

const line = (items: { name: string; value: number; unit: string }[] | undefined) =>
  items && items.length > 0 ? items.map((m) => `${words(m.name)} ${number(m.value, Math.abs(m.value) < 10 ? 2 : 0, m.unit)}`).join("; ") : "none stated";

/**
 * The approval decision (UX-03). It restates what is being approved - action and class, target, who asked and in which role, the
 * reason and the expected benefit and harm, the constraints and the expiry - says the four-eyes and policy facts in words, defaults
 * focus to Cancel, and shows the server's own answer in place. Approving does not execute anything.
 */
export function ReviewDialog({ commandId, open, now, onClose, onDone }: { commandId: string | null; open: boolean; now: number; onClose: () => void; onDone: () => void }) {
  const { state } = useAuth();
  const me = state.status === "authenticated" ? state.me.username : "";
  const detail = useApi<CommandDetail>(open && commandId ? `/api/v1/commands/${commandId}` : null);
  const decide = useAction();
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | null>(null);
  const [result, setResult] = useState<ConfirmResult | null>(null);

  useEffect(() => {
    if (open) {
      setReason("");
      setReasonError(null);
      setResult(null);
    }
  }, [open, commandId]);

  const loaded = detail.data;

  const command = loaded?.command;
  const safetyClass = loaded?.safety_class ?? "";
  const role = loaded?.requested_by_role ?? null;
  const params = loaded?.params ?? null;
  const recommendation = loaded?.recommendation ?? null;
  const basis = (params as { basis?: Basis } | null)?.basis;
  const mine = command?.requested_by === me;
  const waiting = command?.status === "requested";
  const decideOn = async (decision: "approve" | "deny") => {
    if (!command) return;
    if (decision === "deny" && !reason.trim()) {
      setReasonError("Say why you are denying it.");
      return;
    }
    setReasonError(null);
    const outcome = await decide.run(() => api<{ status: string; error: { message: string } | null }>(`/api/v1/commands/${command.command_id}/review`, { method: "POST", body: { decision, reason: reason.trim() || undefined } }));
    if (outcome.ok) {
      const status = outcome.value.status;
      setResult(
        status === "approved"
          ? { tone: "ok", title: "Approved", message: "The policy checked it again against what is true now and it passed. The command executor will run it; nothing has been executed yet." }
          : status === "requested"
            ? { tone: "warn", title: "Still waiting", message: `The policy check could not run (${outcome.value.error?.message ?? "outage"}). Nothing was approved. Try again once the policy is available.` }
            : { tone: "danger", title: status === "expired" ? "Expired" : "Denied", message: outcome.value.error?.message ?? "The command was not approved." },
      );
      onDone();
    } else {
      setResult({ tone: "danger", title: "Not recorded", message: outcome.error.message });
      if (outcome.error.code === "invalid_transition" || outcome.error.code === "four_eyes") onDone();
    }
  };

  return (
    <Dialog open={open} title={command ? `Review: ${ACTION_WORDS[command.action_type] ?? words(command.action_type)}` : "Review a command"} onClose={decide.busy ? () => undefined : onClose}>
      {!command ? (
        <>
          {detail.error ? <p role="alert">This command could not be loaded.</p> : <LoadingState what="the command" />}
          <div className="dialog-actions">
            <Button data-autofocus variant="secondary" onClick={onClose}>
              Close
            </Button>
          </div>
        </>
      ) : (
        <ConfirmBody
          summary={[
            { label: "Action", value: ACTION_WORDS[command.action_type] ?? words(command.action_type) },
            { label: "Safety class", value: SAFETY_WORDS[safetyClass] ?? safetyClass },
            { label: "Target", value: `${command.target.entity_id} (${words(command.target.adapter)})` },
            { label: "Requested by", value: `${command.requested_by}${role ? ` (${words(role)})` : ""}, ${utcDateTime(command.requested_at)}` },
            { label: "Reason", value: basis?.description ?? "No reason was recorded with the request." },
            { label: "Expected benefit", value: line(basis?.predicted_benefit) },
            { label: "Expected harm", value: line(basis?.predicted_harm) },
            { label: "Constraints", value: basis?.constraints && basis.constraints.length > 0 ? basis.constraints.map(words).join(", ") : "none stated" },
            { label: "Expires", value: `${utcDateTime(command.expires_at)} (${expiresIn(command.expires_at, now)})` },
            ...(params && Object.keys(params).filter((k) => k !== "basis").length > 0
              ? [{ label: "Settings", value: Object.entries(params).filter(([k]) => k !== "basis").map(([k, v]) => `${words(k)} ${String(v)}`).join("; ") }]
              : []),
          ]}
          statements={[
            <>Four eyes: {mine ? "you requested this command, so you cannot approve it." : `${command.requested_by} requested it and you are a different person, so you may decide.`}</>,
            <>Policy: not checked yet. It runs when you approve, against the live network, and can still deny it.</>,
            <>Approving does not execute anything. The command executor service executes approved commands; no person does.</>,
            recommendation ? <>From a recommendation: <Link to="/actions/recommendations">see recommendations</Link>.</> : <>Requested directly, not from a recommendation.</>,
          ]}
          confirmLabel="Approve"
          canConfirm={Boolean(waiting) && !mine}
          cannotConfirmBecause={!waiting ? `This command is ${command.status}; it can no longer be decided.` : mine ? "You requested this command. Someone else has to approve or deny it." : undefined}
          denyLabel="Deny"
          onDeny={() => void decideOn("deny")}
          busy={decide.busy}
          result={result}
          onConfirm={() => void decideOn("approve")}
          onClose={onClose}
        >
          {waiting && !mine ? <TextField label="Reason (required to deny)" value={reason} onChange={setReason} multiline error={reasonError} /> : null}
        </ConfirmBody>
      )}
    </Dialog>
  );
}
