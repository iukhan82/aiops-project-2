import type { Step } from "../../components/lifecycle";
import { statusEntry } from "../../lib/status";

export const CALL_FLOW = ["received", "dispatched", "unit_assigned", "en_route", "on_scene", "cleared"] as const;

/** The same table as `backend/repositories/emergency.py`. The API decides; this only chooses which controls to offer. */
export const ASSIGNMENT_ALLOWED: Record<string, readonly string[]> = {
  assigned: ["acknowledged", "unavailable"],
  acknowledged: ["en_route", "unavailable"],
  en_route: ["staged", "on_scene", "unavailable"],
  staged: ["on_scene", "unavailable"],
  on_scene: ["clear", "unavailable"],
  clear: [],
  unavailable: [],
};

export const ASSIGNMENT_FLOW = ["assigned", "acknowledged", "en_route", "on_scene", "clear"] as const;

export const ACTION_LABEL: Record<string, string> = {
  acknowledged: "Unit acknowledged",
  en_route: "Unit en route",
  staged: "Unit staged",
  on_scene: "Unit on scene",
  clear: "Unit clear",
  unavailable: "Mark unit unavailable",
};

export const label = (domain: "call" | "assignment", status: string): string => statusEntry(domain, status).label;

/** Read-only steps for a call. A call follows its units; it is not moved by hand except to cancel it. */
export function callSteps(status: string): Step[] {
  const at = (CALL_FLOW as readonly string[]).indexOf(status);
  return CALL_FLOW.map((id, index) => ({ id, label: label("call", id), state: status === "cancelled" ? (index === 0 ? "done" : "unavailable") : index < at ? "done" : index === at ? "current" : "upcoming" }));
}

export function assignmentSteps(status: string): Step[] {
  if (status === "unavailable") return [{ id: "unavailable", label: label("assignment", "unavailable"), state: "current" }];
  const flow: string[] = status === "staged" ? ["assigned", "acknowledged", "en_route", "staged", "on_scene", "clear"] : [...ASSIGNMENT_FLOW];
  const at = flow.indexOf(status);
  return flow.map((id, index) => ({ id, label: label("assignment", id), state: index < at ? "done" : index === at ? "current" : "upcoming" }));
}

export const unitKind = (unitId: string): string => (unitId.startsWith("ambulance") ? "ambulance" : unitId.startsWith("fire-engine") ? "fire" : "police");
