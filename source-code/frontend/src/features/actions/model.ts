import type { Alternative, CommandRecord, Incident, Recommendation, Topology } from "../../api/types";
import type { Step } from "../../components/lifecycle";
import { number, words } from "../../lib/format";
import { statusEntry } from "../../lib/status";

/** The status vocabulary has one more state than the database: a command still waiting whose policy check could not run. */
export function commandState(command: Pick<CommandRecord, "status" | "policy_decision">): string {
  return command.status === "requested" && command.policy_decision === "policy_unavailable" ? "requested_policy_unavailable" : command.status;
}

export const commandLabel = (state: string): string => statusEntry("command", state).label;

const MAIN = ["requested", "approved", "executing", "executed"] as const;

/** The path a command took, ending where it ended. Each state is a distinct shape and word; a refusal or failure never reads as progress. */
export function commandSteps(command: Pick<CommandRecord, "status" | "policy_decision">): Step[] {
  const state = commandState(command);
  let path: string[];
  switch (state) {
    case "denied":
    case "expired":
      path = ["requested", state];
      break;
    case "failed":
      path = ["requested", "approved", "executing", "failed"];
      break;
    case "rolled_back":
      path = ["requested", "approved", "executing", "executed", "rolled_back"];
      break;
    case "requested_policy_unavailable":
      path = ["requested_policy_unavailable", "approved", "executing", "executed"];
      break;
    default:
      path = [...MAIN];
  }
  const at = path.indexOf(state === "requested_policy_unavailable" ? state : state);
  return path.map((id, index) => ({ id, label: commandLabel(id), state: index < at ? "done" : index === at ? "current" : "upcoming" }));
}

/** Waiting commands first (they need a person), then the rest newest first. */
export function commandOrder(a: CommandRecord, b: CommandRecord): number {
  const waiting = (c: CommandRecord) => (c.status === "requested" ? 0 : 1);
  return waiting(a) - waiting(b) || b.requested_at.localeCompare(a.requested_at);
}

export const ACTION_WORDS: Record<string, string> = {
  diversion: "Divert traffic",
  signal_plan_change: "Change signal timing",
  variable_message_sign: "Show a message on a sign",
  transit_priority: "Give transit priority",
  emergency_preemption: "Pre-empt signals for an emergency vehicle",
  other: "Other action",
};

export const SAFETY_WORDS: Record<string, string> = {
  "SC-0": "SC-0 (advisory: humans decide whether to follow it)",
  "SC-1": "SC-1 (changes how the network operates)",
  "SC-2": "SC-2 (emergency or critical: highest scrutiny)",
};

export interface MetricLine {
  name: string;
  value: string;
}

export function metrics(value: Alternative["predicted_benefit"]): MetricLine[] {
  if (Array.isArray(value)) return (value as { name: string; value: number; unit: string }[]).map((m) => ({ name: words(m.name), value: number(m.value, Math.abs(m.value) < 10 ? 2 : 0, m.unit === "s" ? "s" : m.unit) }));
  return [];
}

export function isNoAction(alternative: Alternative): boolean {
  return String(alternative.description ?? "").toLowerCase().startsWith("take no action");
}

/** What the server will act on, worked out the way the server works it out, so the confirmation can name a target. The API stays the authority. */
export function targetPreview(rec: Recommendation, incident: Incident | undefined, topology: Topology | null): { adapter: string; entity: string } | null {
  if (!incident) return null;
  const segment = incident.network_element_type === "lane" ? incident.network_element_id.replace(/_[^_]+$/, "") : incident.network_element_id;
  if (rec.action_type === "diversion") return { adapter: "diversion adapter", entity: segment };
  if (rec.action_type === "signal_plan_change") {
    const to = topology?.segments.find((s) => s.edge_id === segment)?.to_node;
    return to ? { adapter: "signal controller adapter", entity: to } : null;
  }
  return null;
}

export function expiresIn(expiresAt: string, now: number): string {
  const seconds = Math.round((Date.parse(expiresAt) - now) / 1000);
  if (seconds <= 0) return "expired";
  if (seconds < 90) return `in ${seconds} s`;
  return `in ${Math.round(seconds / 60)} min`;
}
