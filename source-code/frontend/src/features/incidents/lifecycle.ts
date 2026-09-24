import type { Step } from "../../components/lifecycle";

export const FLOW = ["open", "acknowledged", "investigating", "escalated", "resolved"] as const;

/** The same table as `backend/repositories/incidents.py`. The API is the authority; this only decides which controls to offer. */
export const ALLOWED: Record<string, readonly string[]> = {
  open: ["acknowledged", "investigating", "escalated", "resolved"],
  acknowledged: ["investigating", "escalated", "resolved"],
  investigating: ["escalated", "resolved"],
  escalated: ["investigating", "resolved"],
  resolved: ["reopened"],
  reopened: ["acknowledged", "investigating", "escalated", "resolved"],
};

export const LABEL: Record<string, string> = {
  open: "Open",
  acknowledged: "Acknowledged",
  investigating: "Investigating",
  escalated: "Escalated",
  resolved: "Resolved",
  reopened: "Reopened",
};

export const DESKS: Record<string, string> = {
  OPS: "Traffic operations",
  EMERG: "Emergency desk",
  CONTROL: "Control desk",
  BACKEND: "Backend on-call",
  SEC: "Security",
};

export const desk = (code: string | null | undefined): string => (code ? `${DESKS[code] ?? code}` : "Unassigned");

/** Steps for the stepper. Done, current and upcoming come from the status; only a legal next step (for someone who may manage it) is a control. */
export function stepsFor(status: string, canManage: boolean, choose: (to: string) => void, busy: boolean): Step[] {
  const flow: string[] = status === "reopened" ? ["open", "reopened", "acknowledged", "investigating", "escalated", "resolved"] : [...FLOW];
  const at = flow.indexOf(status);
  const legal = new Set(ALLOWED[status] ?? []);
  return flow.map((id, index) => ({
    id,
    label: LABEL[id] ?? id,
    state: index < at ? "done" : index === at ? "current" : "upcoming",
    onSelect: canManage && legal.has(id) ? () => choose(id) : undefined,
    busy: busy && legal.has(id),
  }));
}
