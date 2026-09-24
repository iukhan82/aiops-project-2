/** Shapes of the API responses the screens read. They mirror the contracts in source-code/contracts and backend/api. */

export interface Intersection {
  intersection_id: string;
  corridor_id: string | null;
  latitude: number;
  longitude: number;
}

export interface Segment {
  edge_id: string;
  from_node: string;
  to_node: string;
  corridor_id: string | null;
  direction: string;
  order_index: number | null;
  length_m: number;
  free_flow_speed_m_s: number;
}

export interface Topology {
  geometry_version: string;
  coordinate_reference: string;
  intersections: Intersection[];
  segments: Segment[];
}

export interface Measurement {
  name: string;
  value: number | string | boolean;
  unit: string;
  quality: "valid" | "suspect" | "invalid" | string;
  confidence: number;
  sample_count?: number;
  uncertainty?: { method: string; lower_bound: number; upper_bound: number };
}

export interface NetworkState {
  record_id: string;
  network_element_type: string;
  network_element_id: string;
  geometry_version: string;
  window_seconds: number;
  observation_time: string;
  ingest_time: string;
  measurements: Measurement[];
  truth_label: string;
  freshness_status: "fresh" | "stale" | "unknown";
  max_staleness_seconds: number;
}

export interface NetworkStateList {
  element_type: string;
  geometry_version: string;
  window_seconds: number;
  max_staleness_seconds: number;
  items: NetworkState[];
  /** Source time of the newest reading behind each record. The record's own observation_time is the end of its window. */
  newest_sample_time: Record<string, string | null>;
  as_of: string | null;
  unobserved: string[];
}

export interface Device {
  device_id: string;
  device_type: string;
  deployment_type: string;
  agency_scope: string;
  status: string;
  corridor_id: string | null;
  intersection_id: string | null;
  lane_id: string | null;
  registered_at: string;
  certificate_not_after: string | null;
  last_observation_time: string | null;
}

export interface Observation {
  event_id: string;
  device_id: string;
  event_type: string;
  observation_time: string;
  measurements: Measurement[];
  truth_label: string;
  latitude: number | null;
  longitude: number | null;
}

export interface LiveEvent {
  event_id: string;
  device_id: string;
  event_type: string;
  observation_time: string;
  received_at: string;
  measurements: Measurement[];
}

export interface Incident {
  incident_id: string;
  incident_type: string;
  severity: string;
  status: string;
  network_element_type: string;
  network_element_id: string;
  geometry_version: string;
  opened_at: string;
  updated_at: string;
  resolved_at?: string;
  evidence_event_ids: string[];
  root_cause_hypothesis?: string;
  verified_cause?: string;
  owner_role: string | null;
  confidence: number;
  duplicate_of?: string;
}

export interface EmergencyCall {
  call_id: string;
  call_type: string;
  call_subtype: string;
  priority: string;
  location: { coordinate_reference: string; latitude: number; longitude: number };
  geometry_version: string;
  reported_at: string;
  source_reliability: string;
  status: string;
  truth_label: string;
}

export interface Forecast {
  forecast_id: string;
  network_element_type: string;
  network_element_id: string;
  geometry_version: string;
  predicted_at: string;
  horizon_seconds: number;
  valid_from: string;
  valid_until: string;
  model_id: string;
  model_version: string;
  baseline_id?: string;
  measurements: Measurement[];
  truth_label: string;
}

export interface Candidate {
  candidate_id: string;
  kind: string;
  network_element_type: string;
  network_element_id: string;
  geometry_version: string;
  onset_time: string;
  clear_time: string | null;
  detected_at: string;
  severity: string;
  confidence: number;
  evidence_event_ids: string[];
  source: string;
  attributes: Record<string, unknown>;
  truth_label: string;
}

export interface RouteAlternative {
  route_id: string;
  geometry_version: string;
  distance_m: number;
  eta_seconds: number;
  eta_uncertainty_seconds: number;
  constraints_applied?: string[];
  selected: boolean;
  edges?: string[];
}

export interface Assignment {
  assignment_id: string;
  call_id: string;
  unit_id: string;
  agency: string;
  capability: string[];
  status: string;
  assigned_at: string;
  acknowledged_at?: string;
  arrived_at?: string;
  cleared_at?: string;
  route_alternatives: RouteAlternative[];
  truth_label: string;
}

export interface Transition {
  from_status: string | null;
  to_status: string;
  changed_by: string;
  note: string | null;
  changed_at: string;
}

export interface CallDetail {
  call: EmergencyCall;
  assignments: Assignment[];
  transitions: Transition[];
  assignment_transitions: (Transition & { assignment_id: string; unit_id: string })[];
}

export interface Recommendation {
  recommendation_id: string;
  trigger_incident_id?: string;
  trigger_emergency_call_id?: string;
  action_type: string;
  generated_at: string;
  expires_at: string;
  status: string;
  alternatives: Alternative[];
  safety_bounds: Record<string, unknown>;
  constraints: string[] | Record<string, unknown>;
}

export interface Alternative {
  alternative_id?: string;
  description?: string;
  predicted_benefit?: Record<string, unknown> | string;
  predicted_harm?: Record<string, unknown> | string;
  [key: string]: unknown;
}

export interface CommandRecord {
  command_id: string;
  idempotency_key: string;
  recommendation_id?: string;
  action_type: string;
  target: { adapter: string; entity_id: string };
  requested_by: string;
  requested_at: string;
  approved_by?: string;
  approved_at?: string;
  expires_at: string;
  policy_decision: string;
  status: string;
  acknowledged_at?: string;
  rolled_back_at?: string;
  error?: { error_code: string; message: string; retryable: boolean };
}

export interface RouteResult {
  origin: string;
  destination: string;
  geometry_version: string;
  route_alternatives: RouteAlternative[];
}

export interface OutcomeMeasurement {
  name: string;
  value: number;
  unit: string;
}

export interface Outcome {
  outcome_id: string;
  command_id: string;
  pre_window: { from: string; to: string; measurements: OutcomeMeasurement[] };
  post_window: { from: string; to: string; measurements: OutcomeMeasurement[] };
  classification: string;
  verified_at: string;
  verifier: string;
  rollback_triggered: boolean;
  escalation_reason?: string;
}

export interface CommandDetail {
  command: CommandRecord;
  transitions: Transition[];
  safety_class: string;
  requested_by_role: string | null;
  approved_by_role: string | null;
  params: Record<string, unknown> | null;
  outcome: Outcome | null;
  recommendation: { recommendation_id: string; action_type: string; status: string; trigger_incident_id: string | null; trigger_emergency_call_id: string | null } | null;
}
