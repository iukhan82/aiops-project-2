import type { Device, EmergencyCall, Incident, LiveEvent, NetworkState, Observation, Segment, Topology } from "../../api/types";
import { ageSeconds, number, utcClock, words } from "../../lib/format";
import type { Domain } from "../../lib/status";
import {
  ACTIVE_INCIDENT,
  closedSegments,
  flowClass,
  kmh,
  makeProjection,
  measurementOf,
  segmentIdOf,
  segmentLine,
  type FlowClass,
  type Point,
  type Projection,
} from "./geometry";

export type ItemKind = "segment" | "intersection" | "device" | "incident" | "call" | "unit";
export type Freshness = "fresh" | "stale" | "unknown" | null;

export const KIND_LABEL: Record<ItemKind, string> = {
  segment: "Road segment",
  intersection: "Intersection",
  device: "Device",
  incident: "Incident",
  call: "Emergency call",
  unit: "Emergency unit",
};

/** After this long without a reading a device is shown stale. Loops and SPAT report every few seconds to a minute; weather and road sensors slowly. */
export const DEVICE_STALE_AFTER_S: Record<string, number> = {
  inductive_loop: 120,
  traffic_loop: 120,
  cycle_counter: 120,
  crossing_detector: 300,
  signal_controller: 120,
  edge_camera: 180,
  weather_station: 600,
  road_condition_sensor: 600,
  emergency_cad_avl_adapter: 60,
};

export interface MapItem {
  id: string;
  kind: ItemKind;
  /** The element's own name, shown in list and panel. */
  name: string;
  status: { domain: Domain; value: string } | null;
  /** The reading in words with units, or why there is none. */
  reading: string;
  observedAt: string | null;
  truth: string | null;
  freshness: Freshness;
  /** Present when the element can be drawn on the schematic. */
  point?: Point;
  line?: { a: Point; b: Point; mid: Point };
  flow?: FlowClass;
  closed?: boolean;
  severity?: string;
  href?: string;
  details: { label: string; value: string }[];
}

export interface Layers {
  traffic: boolean;
  devices: boolean;
  incidents: boolean;
  emergency: boolean;
}

export interface ModelInput {
  topology: Topology;
  states: readonly NetworkState[];
  /** Source time of the newest reading behind each segment's state (the state's own time is only the end of its window). */
  newestSample: ReadonlyMap<string, string | null>;
  devices: readonly Device[];
  incidents: readonly Incident[];
  calls: readonly EmergencyCall[];
  units: readonly Observation[];
  live: ReadonlyMap<string, LiveEvent>;
  now: number;
  canOpenIncident: boolean;
  canOpenCall: boolean;
}

export interface MapModel {
  projection: Projection;
  items: MapItem[];
}

function flowWords(flow: FlowClass): string {
  return { flowing: "flowing", slow: "slow", congested: "congested", unknown: "no speed reading" }[flow];
}

function segmentItem(segment: Segment, state: NetworkState | undefined, newest: string | null | undefined, closed: Incident | undefined, points: ReadonlyMap<string, Point>, sides: ReadonlyMap<string, number>): MapItem {
  const from = points.get(segment.from_node);
  const to = points.get(segment.to_node);
  const flow = flowClass(state, segment);
  const speed = kmh(measurementOf(state, "mean_speed"));
  const occupancy = measurementOf(state, "occupancy");
  const count = measurementOf(state, "vehicle_count");
  const reading = state
    ? [speed !== null ? `${number(speed, 0, "km/h")}` : null, occupancy !== null ? `occupancy ${number(occupancy * 100, 0, "%")}` : null, count !== null ? `${number(count, 0)} vehicles` : null, flowWords(flow)]
        .filter(Boolean)
        .join(", ")
    : "no observation yet";
  return {
    id: `segment:${segment.edge_id}`,
    kind: "segment",
    name: `${segment.from_node} to ${segment.to_node}`,
    status: null,
    reading: closed ? `closed (${words(closed.incident_type)}); ${reading}` : reading,
    observedAt: newest ?? null,
    truth: state?.truth_label ?? null,
    freshness: state ? state.freshness_status : "unknown",
    line: from && to ? segmentLine(from, to, sides.get(segment.edge_id) ?? 0) : undefined,
    flow,
    closed: Boolean(closed),
    details: [
      { label: "Corridor", value: segment.corridor_id ? `${segment.corridor_id}, ${segment.direction}` : `cross street (${segment.direction})` },
      { label: "Length", value: number(segment.length_m, 0, "m") },
      { label: "Free-flow speed", value: number(segment.free_flow_speed_m_s * 3.6, 0, "km/h") },
      { label: "Window", value: state ? `${number(state.window_seconds, 0, "s")} average` : "not available" },
    ],
  };
}

function devicePoint(device: Device, points: ReadonlyMap<string, Point>, segmentMids: ReadonlyMap<string, Point>, slot: number): Point | undefined {
  const base = device.intersection_id ? points.get(device.intersection_id) : device.lane_id ? segmentMids.get(segmentIdOf("lane", device.lane_id) ?? "") : undefined;
  if (!base) return undefined;
  const angle = (slot * 2 * Math.PI) / 6 + Math.PI / 6;
  const radius = device.intersection_id ? 22 : 14;
  return { x: base.x + Math.cos(angle) * radius, y: base.y + Math.sin(angle) * radius };
}

/** Everything the map shows, as one list, so the canvas, the accessible list and the selection panel can never disagree. */
export function buildModel(input: ModelInput): MapModel {
  const projection = makeProjection(input.topology.intersections);
  const points = new Map<string, Point>(input.topology.intersections.map((i) => [i.intersection_id, projection.point(i.latitude, i.longitude)]));
  const stateById = new Map(input.states.map((s) => [s.network_element_id, s]));
  const closed = closedSegments(input.incidents);
  // Both directions of a road are drawn side by side. The perpendicular offset flips with the direction of travel, so
  // every segment that has a reverse partner uses the same sign; a one-way segment stays on the centre line.
  const sides = new Map<string, number>();
  const directed = new Set(input.topology.segments.map((s) => `${s.from_node}>${s.to_node}`));
  for (const s of input.topology.segments) sides.set(s.edge_id, directed.has(`${s.to_node}>${s.from_node}`) ? 1 : 0);
  const items: MapItem[] = [];

  for (const segment of input.topology.segments) items.push(segmentItem(segment, stateById.get(segment.edge_id), input.newestSample.get(segment.edge_id), closed.get(segment.edge_id), points, sides));
  const segmentMids = new Map(items.filter((i) => i.kind === "segment" && i.line).map((i) => [i.id.slice("segment:".length), i.line!.mid]));

  for (const intersection of input.topology.intersections) {
    items.push({
      id: `intersection:${intersection.intersection_id}`,
      kind: "intersection",
      name: intersection.intersection_id,
      status: null,
      reading: "junction",
      observedAt: null,
      truth: null,
      freshness: null,
      point: points.get(intersection.intersection_id),
      details: [
        { label: "Corridor", value: intersection.corridor_id ?? "not on a corridor" },
        { label: "Position", value: `${number(intersection.latitude, 5)}, ${number(intersection.longitude, 5)}` },
      ],
    });
  }

  const slotCount = new Map<string, number>();
  for (const device of input.devices) {
    const anchor = device.intersection_id ?? device.lane_id ?? "none";
    const slot = slotCount.get(anchor) ?? 0;
    slotCount.set(anchor, slot + 1);
    const liveEvent = input.live.get(device.device_id);
    const observedAt = liveEvent && (!device.last_observation_time || liveEvent.observation_time > device.last_observation_time) ? liveEvent.observation_time : device.last_observation_time;
    const age = ageSeconds(observedAt, input.now);
    const staleAfter = DEVICE_STALE_AFTER_S[device.device_type] ?? 300;
    const freshness: Freshness = age === null ? "unknown" : age > staleAfter ? "stale" : "fresh";
    const latest = liveEvent?.measurements
      .map((m) => `${words(m.name)} ${typeof m.value === "number" ? number(m.value, Number.isInteger(m.value) ? 0 : 2) : String(m.value)}${m.unit && m.unit !== "count" ? ` ${m.unit}` : ""}`)
      .join(", ");
    items.push({
      id: `device:${device.device_id}`,
      kind: "device",
      name: device.device_id,
      status: { domain: "device", value: device.status },
      reading: latest ? `latest: ${latest}` : observedAt ? "reporting" : "never reported",
      observedAt,
      truth: device.deployment_type === "simulated" ? "simulated" : "measured",
      freshness,
      point: devicePoint(device, points, segmentMids, slot),
      details: [
        { label: "Type", value: words(device.device_type) },
        { label: "Agency", value: device.agency_scope },
        { label: "Location", value: device.intersection_id ?? device.lane_id ?? device.corridor_id ?? "not placed" },
        { label: "Certificate valid until", value: device.certificate_not_after ? utcClock(device.certificate_not_after) : "not recorded" },
      ],
    });
  }

  for (const incident of input.incidents) {
    if (incident.duplicate_of) continue;
    const segmentId = segmentIdOf(incident.network_element_type, incident.network_element_id);
    const point = segmentId ? segmentMids.get(segmentId) : incident.network_element_type === "intersection" ? points.get(incident.network_element_id) : undefined;
    items.push({
      id: `incident:${incident.incident_id}`,
      kind: "incident",
      name: `${words(incident.incident_type)} at ${incident.network_element_id}`,
      status: { domain: "incident", value: incident.status },
      reading: `${incident.severity} severity, confidence ${number(incident.confidence * 100, 0, "%")}`,
      observedAt: incident.updated_at,
      truth: "inferred",
      freshness: null,
      point: point ? { x: point.x, y: point.y - 16 } : undefined,
      severity: incident.severity,
      href: input.canOpenIncident ? `/incidents/${incident.incident_id}` : undefined,
      details: [
        { label: "Severity", value: incident.severity },
        { label: "Opened", value: utcClock(incident.opened_at) },
        { label: "Owner", value: incident.owner_role ? words(incident.owner_role) : "unassigned" },
        { label: "Cause", value: incident.verified_cause ?? "not verified" },
      ],
    });
  }

  for (const call of input.calls) {
    items.push({
      id: `call:${call.call_id}`,
      kind: "call",
      name: `${words(call.call_type)}: ${words(call.call_subtype)}`,
      status: { domain: "call", value: call.status },
      reading: `${call.priority} priority`,
      observedAt: call.reported_at,
      truth: call.truth_label,
      freshness: null,
      point: projection.point(call.location.latitude, call.location.longitude),
      severity: call.priority,
      href: input.canOpenCall ? `/dispatch/${call.call_id}` : undefined,
      details: [
        { label: "Reported", value: utcClock(call.reported_at) },
        { label: "Source", value: words(call.source_reliability) },
      ],
    });
  }

  for (const unit of input.units) {
    if (unit.latitude === null || unit.longitude === null) continue;
    const speed = unit.measurements.find((m) => m.name === "speed");
    const age = ageSeconds(unit.observation_time, input.now);
    items.push({
      id: `unit:${unit.device_id}`,
      kind: "unit",
      name: unit.device_id.replace(/^avl-/, ""),
      status: null,
      reading: typeof speed?.value === "number" ? `moving at ${number(kmh(speed.value), 0, "km/h")}` : "position only",
      observedAt: unit.observation_time,
      truth: unit.truth_label,
      freshness: age === null ? "unknown" : age > 60 ? "stale" : "fresh",
      point: projection.point(unit.latitude, unit.longitude),
      details: [{ label: "Source device", value: unit.device_id }],
    });
  }

  return { projection, items };
}

export function activeIncidents(incidents: readonly Incident[]): Incident[] {
  return incidents.filter((i) => ACTIVE_INCIDENT.has(i.status) && !i.duplicate_of);
}

export function visibleOnMap(item: MapItem, layers: Layers): boolean {
  if (item.kind === "segment" || item.kind === "intersection") return layers.traffic;
  if (item.kind === "device") return layers.devices;
  if (item.kind === "incident") return layers.incidents;
  return layers.emergency;
}

/** The sentence a screen reader gets for an element: kind, name, reading, and how fresh it is. */
export function accessibleName(item: MapItem): string {
  const parts = [`${KIND_LABEL[item.kind]} ${item.name}`, item.reading];
  if (item.status) parts.push(item.status.value.replace(/_/g, " "));
  if (item.freshness) parts.push(item.freshness === "unknown" ? "freshness unknown" : item.freshness);
  if (item.observedAt) parts.push(`observed ${utcClock(item.observedAt)}`);
  return parts.join(", ");
}
