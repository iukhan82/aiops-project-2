import type { Incident, Intersection, NetworkState, Segment } from "../../api/types";

export interface Point {
  x: number;
  y: number;
}

export interface Projection {
  width: number;
  height: number;
  point: (latitude: number, longitude: number) => Point;
}

/** Equirectangular projection fitted to the intersections, with the district's real aspect ratio (a schematic, not a survey). */
export function makeProjection(intersections: readonly Intersection[], width = 1000, pad = 70): Projection {
  if (intersections.length === 0) return { width, height: width / 2, point: () => ({ x: width / 2, y: width / 4 }) };
  const lat0 = intersections.reduce((sum, i) => sum + i.latitude, 0) / intersections.length;
  const kx = Math.cos((lat0 * Math.PI) / 180);
  const xs = intersections.map((i) => i.longitude * kx);
  const ys = intersections.map((i) => i.latitude);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 1e-9);
  const spanY = Math.max(maxY - minY, 1e-9);
  const scale = (width - 2 * pad) / spanX;
  const height = Math.round(spanY * scale + 2 * pad);
  return {
    width,
    height,
    point: (latitude, longitude) => ({ x: pad + (longitude * kx - minX) * scale, y: pad + (maxY - latitude) * scale }),
  };
}

/** Two directed segments between the same nodes are drawn side by side, offset perpendicular to the road. */
export function segmentLine(from: Point, to: Point, side: number, gap = 9): { a: Point; b: Point; mid: Point } {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy) || 1;
  const nx = (-dy / length) * gap * side;
  const ny = (dx / length) * gap * side;
  const a = { x: from.x + nx, y: from.y + ny };
  const b = { x: to.x + nx, y: to.y + ny };
  return { a, b, mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
}

export type FlowClass = "flowing" | "slow" | "congested" | "unknown";

/** Speed as a share of the segment's free-flow speed. The thresholds are shown in the legend, not hidden in the code. */
export const FLOW_THRESHOLDS = { slowBelow: 0.7, congestedBelow: 0.4 } as const;

export function measurementOf(state: NetworkState | undefined, name: string): number | null {
  const m = state?.measurements.find((x) => x.name === name);
  return m && typeof m.value === "number" ? m.value : null;
}

export function flowClass(state: NetworkState | undefined, segment: Segment): FlowClass {
  const speed = measurementOf(state, "mean_speed");
  if (speed === null || !segment.free_flow_speed_m_s) return "unknown";
  const ratio = speed / segment.free_flow_speed_m_s;
  if (ratio < FLOW_THRESHOLDS.congestedBelow) return "congested";
  if (ratio < FLOW_THRESHOLDS.slowBelow) return "slow";
  return "flowing";
}

export const CLOSURE_KINDS = new Set(["stalled_vehicle", "collision", "wrong_way", "flooding"]);
export const ACTIVE_INCIDENT = new Set(["open", "acknowledged", "investigating", "escalated", "reopened"]);

/** A segment is shown closed under the same rule the router uses: a corroborated high or critical incident of a blocking kind. */
export function closedSegments(incidents: readonly Incident[]): Map<string, Incident> {
  const closed = new Map<string, Incident>();
  for (const incident of incidents) {
    if (!ACTIVE_INCIDENT.has(incident.status) || !CLOSURE_KINDS.has(incident.incident_type)) continue;
    if (incident.severity !== "high" && incident.severity !== "critical") continue;
    const id = segmentIdOf(incident.network_element_type, incident.network_element_id);
    if (id) closed.set(id, incident);
  }
  return closed;
}

/** Lane ids are `<segment id>_<lane index>`; anything else that is not a segment has no single line on the schematic. */
export function segmentIdOf(elementType: string, elementId: string): string | null {
  if (elementType === "segment") return elementId;
  if (elementType === "lane") {
    const at = elementId.lastIndexOf("_");
    return at > 0 ? elementId.slice(0, at) : null;
  }
  return null;
}

export function kmh(metresPerSecond: number | null): number | null {
  return metresPerSecond === null ? null : metresPerSecond * 3.6;
}
