import { number, words } from "../../lib/format";

export interface AuditRecord {
  audit_id: string;
  at: string;
  source: string;
  actor: string | null;
  actor_roles: string[];
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  outcome: string;
  detail: Record<string, unknown>;
}

export interface ServiceCheck {
  id: string;
  label: string;
  kind: "dependency" | "service" | "worker";
  status: string;
  detail: string;
  latency_ms: number | null;
  checked_at: string;
  last_seen?: string | null;
  age_s?: number | null;
  reported?: Record<string, unknown>;
}

export interface SourceFreshness {
  id: string;
  label: string;
  devices?: number;
  reporting?: number;
  stale?: number;
  never?: number;
  newest_observation_time: string | null;
  age_s: number | null;
  budget_s: number | null;
  status: "fresh" | "stale" | "unknown";
}

export interface Ingestion {
  window_minutes: number;
  events: number;
  lag_p50_s: number | null;
  lag_p95_s: number | null;
  newest_received_at: string | null;
  since_newest_s: number | null;
  rejected: Record<string, number>;
  status: string;
  detail: string;
}

export interface OpsStatus {
  generated_at: string;
  overall: "healthy" | "degraded" | "unknown";
  counts: Record<"healthy" | "degraded" | "down" | "unknown", number>;
  services: ServiceCheck[];
  sources: SourceFreshness[];
  analytics: SourceFreshness[];
  ingestion: Ingestion;
}

export interface OpenItem {
  kind: "incident" | "command" | "call" | "other";
  ref: string | null;
  label: string;
  status: string | null;
  note: string | null;
}

export interface Handover {
  handover_id: string;
  created_at: string;
  author: string;
  author_roles: string[];
  outgoing_shift: string;
  incoming_shift: string;
  summary: string;
  open_items: OpenItem[];
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  status: "awaiting_acknowledgement" | "acknowledged";
}

export interface ScenarioRun {
  run_id: string;
  scenario: string;
  status: string;
  started_by: string;
  started_at: string;
  completed_at: string | null;
  result: { input_count?: number; accepted_count?: number; rejected_count?: number; accepted_sha256?: string; rejected_reasons?: unknown[]; error?: string } | null;
}

export interface DemoAuditRecord {
  audit_id: number;
  at: string;
  actor: string | null;
  role: string;
  action: string;
  outcome: string;
  run_id: string | null;
  detail: string | null;
}

const ACTION_TEXT: Record<string, string> = {
  "command.request": "Requested a command",
  "command.approve": "Approved a command",
  "command.deny": "Denied a command",
  "incident.transition": "Changed an incident's state",
  "incident.owner": "Changed an incident's owner",
  "incident.note": "Added an incident note",
  "call.create": "Took an emergency call",
  "call.transition": "Changed a call's state",
  "assignment.create": "Assigned a unit to a call",
  "assignment.transition": "Changed an assignment's state",
  "assignment.route": "Chose a route for an assignment",
  "handover.create": "Wrote a shift handover",
  "handover.acknowledge": "Acknowledged a shift handover",
};

/** What was done, in words. An action this screen has no wording for is shown as received. */
export function auditActionText(record: Pick<AuditRecord, "action" | "source">): string {
  const known = ACTION_TEXT[record.action];
  if (known) return known;
  if (/^(GET|POST|PUT|PATCH|DELETE|WEBSOCKET) \//.test(record.action)) return `Tried ${record.action}`;
  return words(record.action);
}

export const AUDIT_SOURCE_TEXT: Record<string, string> = {
  operator: "Action through the API",
  command_history: "Command state history",
  incident_history: "Incident state history",
  call_history: "Call state history",
  assignment_history: "Assignment state history",
};

export const ENTITY_TYPE_TEXT: Record<string, string> = {
  incident: "Incident",
  command: "Command",
  recommendation: "Recommendation",
  emergency_call: "Emergency call",
  emergency_assignment: "Assignment",
  handover: "Handover",
  endpoint: "Endpoint",
};

/** Where to read about an entity, or null when there is no screen for it. */
export function entityPath(type: string | null, id: string | null): string | null {
  if (!type || !id) return null;
  if (type === "incident") return `/incidents/${id}`;
  if (type === "command") return `/actions/commands/${id}`;
  if (type === "emergency_call") return `/dispatch/${id}`;
  return null;
}

/** The detail an audit row carries, as short `label value` phrases. Objects and lists are summarised, never dumped. */
export function detailText(detail: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(detail)) {
    if (value === null || value === undefined || value === "") continue;
    const shown = typeof value === "object" ? (Array.isArray(value) ? `${value.length} items` : "recorded") : String(value);
    parts.push(`${words(key).toLowerCase()}: ${shown}`);
  }
  return parts.join("; ");
}

export function itemPath(item: Pick<{ kind: string; ref: string | null }, "kind" | "ref">): string | null {
  if (!item.ref) return null;
  return entityPath(item.kind === "call" ? "emergency_call" : item.kind, item.ref);
}

/** Age as a person reads it: seconds to days, with nothing invented when there is none. */
export function ageWords(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "not measured";
  if (seconds < 90) return `${number(seconds, 0)} s`;
  if (seconds < 5400) return `${number(seconds / 60, 1)} min`;
  return `${number(seconds / 3600, 1)} h`;
}

/** The one line the platform-status screen leads with. Unknowns are counted, never folded into "healthy". */
export function overallSentence(status: Pick<OpsStatus, "counts" | "sources" | "analytics" | "ingestion">): { tone: "info" | "warn" | "danger"; text: string } {
  const { healthy, degraded, down, unknown } = status.counts;
  const stale = [...status.sources, ...status.analytics].filter((s) => s.status === "stale").length;
  const lagging = status.ingestion.status === "degraded" ? 1 : 0;
  const problems: string[] = [];
  if (down) problems.push(`${down} down`);
  if (degraded) problems.push(`${degraded} degraded`);
  if (stale) problems.push(`${stale} ${stale === 1 ? "data source" : "data sources"} stale`);
  if (lagging) problems.push("ingestion is late");
  const tail = unknown ? ` ${unknown} ${unknown === 1 ? "check has" : "checks have"} no answer and ${unknown === 1 ? "is" : "are"} not counted as healthy.` : "";
  if (problems.length > 0) return { tone: down ? "danger" : "warn", text: `Needs attention: ${problems.join(", ")}. ${healthy} healthy.${tail}` };
  if (healthy === 0) return { tone: "warn", text: `Nothing could be confirmed healthy.${tail}` };
  return { tone: "info", text: `${healthy} healthy, nothing degraded, down or stale.${tail}` };
}
