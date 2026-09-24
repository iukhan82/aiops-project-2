# Sensor and Data Catalog

The platform should treat sensors as evidence sources with identity, location,
units, timestamps, quality, calibration, and freshness—not as unquestioned truth.

## 1. Road traffic sensing

| Source | Useful measurements and derived features |
|---|---|
| Inductive loops / magnetometers | Vehicle count, occupancy, presence, headway, approximate speed |
| Radar | Speed, range, lane, direction, object class, stopped vehicle, queue tail |
| LiDAR | Tracks, lane occupancy, trajectories, vulnerable-road-user conflicts |
| Edge camera analytics | Counts by vehicle class, trajectories, queue length, turning movement, stopped/wrong-way events |
| Bluetooth/Wi-Fi travel-time sensor | Anonymous observation ID, segment entry/exit, travel time; strict hashing/retention controls |
| Connected-vehicle messages | Position, heading, speed, acceleration, brake state, vehicle class, event flags |
| Weigh-in-motion | Axle count, vehicle class, weight band, overloaded-vehicle event |
| Probe/fleet feed | Aggregated speed, travel time, route reliability, sample count |

Recommended traffic features include volume by class, mean/median/85th-percentile
speed, lane occupancy, density, headway, acceleration variance, queue length,
queue growth rate, turning movements, lane changes, stopped duration, shockwave
speed, segment travel time, and confidence/sample size.

## 2. Pedestrian and cyclist sensing

| Source | Measurements |
|---|---|
| Crossing detector | Presence, crossing demand, occupancy, clearance time |
| Push-button/controller input | Request time, acknowledgement, service time, device health |
| Radar/LiDAR/video metadata | Count, direction, trajectory, speed, waiting zone, near-conflict indicators |
| Cycle counter | Bicycle count, direction, lane occupancy, speed |
| Accessible crossing device | Request, audible/tactile status, failure state; never store disability identity |

Derived measures should include pedestrian wait, crossing completion, cyclists
served per phase, conflict/near-miss rate, blocked crossing, and accessibility
device availability.

## 3. Signal and roadside controller data

- Signal phase and timing (SPaT): active phase, state, elapsed time, next expected
  change, minimum/maximum end time.
- Intersection geometry (MAP): approaches, lanes, movements, stop lines, crossing
  zones, conflict groups.
- Controller mode: coordinated, actuated, manual, flash, dark, pre-emption, fault.
- Current plan, cycle length, split, offset, detector calls, pedestrian calls,
  transit priority, and emergency pre-emption state.
- Cabinet door, temperature, power, UPS battery, lamp/channel fault, clock drift,
  firmware/configuration version, communications quality, and last heartbeat.
- Variable message sign state, displayed message, brightness, fault, and proof of
  display where supported.

## 4. Weather and road-condition sensing

- Air temperature, humidity, pressure, rainfall intensity and accumulation.
- Visibility, fog, wind speed/gust/direction, lightning proximity.
- Road surface temperature, wet/dry state, water depth, ice probability, friction
  estimate, snow depth, and salinity where applicable.
- Air quality, smoke, dust, and fire indicators for road-safety decisions.
- Weather forecast feed with issue time, valid period, source, confidence, and
  revision identifier.

## 5. Public transport and mobility

- Transit vehicle position, route/trip, heading, speed, delay, occupancy band,
  schedule adherence, next stop, and priority request.
- Stop crowding estimate and service disruption.
- Parking-space occupancy, facility capacity, entry/exit rate, pricing band, and
  EV charger availability.
- Shared mobility availability and geofenced restrictions.
- Roadworks, permits, planned closures, special events, school zones, and freight
  restrictions.

## 6. Emergency-response data

- Incident/call identifier, type, priority, geocoded location, reported time, and
  source reliability.
- Unit identifier, agency, capability, availability, assignment state, position,
  route, ETA, acknowledgement, arrival, clear time, and communications health.
- Route constraints such as vehicle height/weight, hazardous materials, bridge,
  tunnel, road width, and turning limits.
- Facility destination categories and availability status. Patient identifiers
  and medical records remain outside this platform.
- Road closure, perimeter, staging area, evacuation zone, and agency handover.

## 7. Infrastructure and AIOps telemetry

- Device CPU, memory, storage, temperature, power, boot count, uptime, clock
  offset, software/model/configuration version, certificate expiry, and last seen.
- Network latency, jitter, packet loss, signal strength, reconnect count, broker
  queue, stream lag, replay backlog, duplicates, and out-of-order rate.
- Model inference latency, confidence distribution, input drift, output drift,
  abstention rate, feature availability, and disagreement with a baseline.
- Service request rate, latency, errors, saturation, dependency health, trace ID,
  database pressure, consumer lag, and map-layer freshness.

## 8. Human and external reports

- Operator observation with location, time, category, confidence, and attachments.
- Field responder status and structured hazard report.
- Citizen report intake with rate limiting, duplicate detection, moderation, and
  unverified status until corroborated.
- Emergency call/CAD, road authority, weather agency, navigation provider, and
  public transport feeds through versioned adapters.

## 9. Common event envelope

Every event should carry:

- `event_id`, `schema_version`, `event_type`, and `source_type`.
- Source/device identity and tenant/agency scope.
- Observation time, ingest time, sequence number, and clock-quality state.
- Geometry/location, coordinate reference, road/intersection/lane references.
- Measurement values, units, quality flags, confidence, and provenance.
- Simulation/real/inferred/operator-entered classification.
- Privacy classification, retention class, correlation/trace ID, and integrity
  metadata.

## 10. Required data-quality incidents

- Missing or silent sensor.
- Stuck, saturated, impossible, or out-of-range values.
- Conflicting direction/lane/position evidence.
- Clock drift, future timestamp, replay, duplicate, or out-of-order sequence.
- Calibration expiry or abrupt baseline shift.
- Low sample count or confidence collapse.
- Mismatched device identity and network/topic identity.
- Geometry/version mismatch after a road-network or signal-plan change.
