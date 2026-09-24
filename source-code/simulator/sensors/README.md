# P03.03: traffic, VRU, signal, weather and road sensor catalog

Given `(seed, run_id, anchor_utc, sim_end)`, this stage produces a deterministic
device registry (`contracts/device/v1`) and observation-event stream
(`contracts/observation-envelope/v1`) for the P03.01 district network. It is
self-contained: it generates its own demand via
`source-code/simulator/demand/generate_demand.py` rather than depending on
P03.02's output artifacts, so it only needs `network/output/district.net.xml`
(P03.01) to already exist.

## Device catalog (`build_sensor_catalog.py`)

Pure function of the built network file plus the hand-authored node file - no
randomness, no SUMO invocation. Places:

| Device type | Count | Placement |
|---|---|---|
| `inductive_loop` | 18 | one per corridor edge, first general lane |
| `cycle_counter` | 18 | one per corridor edge, dedicated bicycle lane |
| `crossing_detector` | 16 | one per netconvert-generated pedestrian crossing |
| `signal_controller` | 12 | one per intersection |
| `weather_station` | 3 | one per corridor, at that corridor's 2nd intersection |
| `road_condition_sensor` | 3 | colocated with each corridor's weather station |

Total: 70 devices. Cross-street edges (8) carry no sensor in this initial
catalog - a documented scope limitation, not an oversight.

Latitude/longitude are an equirectangular projection of SUMO's local plane
onto an anchor at `(31.5204, 74.3587)` (junction `int-a1` = origin). This is a
flat-earth approximation acceptable for a ~900m x 800m synthetic district; it
is not a geodetic claim about any real place.

## Observation events (`generate_observations.py`)

| Category | Source | Event type(s) |
|---|---|---|
| Traffic | native SUMO `inductionLoop` output (real counts/occupancy/speed) | `traffic.loop_detector.count` |
| VRU (cyclists) | native SUMO `inductionLoop` output on the bicycle lane | `vru.cycle_counter.count` |
| VRU (pedestrians) | seeded synthetic arrival process per crossing site | `vru.crossing_detector.demand`, `vru.crossing_detector.clearance` |
| Signal | native SUMO `SaveTLSStates` output, emitted on phase transition | `signal.controller.spat` |
| Weather | seeded synthetic time series, sampled every 60s | `weather.station.reading` |
| Road condition | seeded synthetic time series, correlated with weather | `road.condition_sensor.reading` |

Every event carries `unit`/`quality`/`confidence` per measurement (schema-enforced),
`device_id` identity, a `location` with `EPSG:4326` coordinates, and a
`provenance.producer`/`pipeline_version` pair. `event_id` is
`uuid5(namespace, "run_id:device_id:sequence_number")`, so it is deterministic
and reproducible rather than random.

**Documented simplification**: pedestrian crossing demand is a seeded Poisson-like
arrival process per crossing site, not derived from individual FCD pedestrian
trajectories - there is no attempt here to geometrically link a specific
simulated pedestrian's walk to the specific crosswalk they would use. Weather
and road-condition readings are a seeded synthetic series (bounded sinusoid +
noise), not a physical weather model. Both are simulated (`truth_label:
"simulated"`) and clearly labelled as such; no field-accuracy claim follows
from them. Fault/anomaly injection (silence, stuck values, clock drift) is
explicitly out of scope here - it is P03.06.

## Reproduction

```bash
bash source-code/simulator/sensors/run_container.sh
python -m pytest source-code/tests/test_sensor_catalog.py -q
```

Requires `source-code/simulator/network/output/district.net.xml` to already
exist (run `network/run_container.sh` first). `build_and_verify.py` (runs
inside the same pinned `ghcr.io/eclipse-sumo/sumo` image as P03.01/P03.02):

1. Builds the device catalog and runs SUMO twice with the same
   `seed=20260918`, `run_id=p03-03-sensors` to produce native loop-detector
   and TLS-state output.
2. Assembles the full observation-event stream from those native outputs plus
   the seeded synthetic VRU/weather/road generators.
3. Asserts the device catalog and the event stream are byte-identical across
   the two runs.
4. Asserts all five required categories (traffic, vru, signal, weather, road)
   produced at least one event.

The pinned SUMO image has no `jsonschema` package, so schema conformance
against `contracts/device/v1` and `contracts/observation-envelope/v1` is
checked separately, on the host pytest venv, by
`source-code/tests/test_sensor_catalog.py` - it reads the git-ignored
`output/run-a/{devices,observations}.jsonl` this script writes.

Last verified run (2026-09-18), `sim_end=600`: 70 devices (18/18/16/12/3/3
across the six types above), 1453 observation events covering all five
required categories, catalog and events byte-identical across two runs,
6/6 host-side contract tests passed (schema conformance x2, category
coverage, unit/quality/confidence presence, identity/location/provenance
presence, cross-run determinism).

## Scope

This is nominal, healthy telemetry only. Traffic/safety scenarios (event,
collision, stall, wrong-way, flood, low visibility, signal fault) are P03.05.
Device/platform fault injection (silence, stuck, clock drift, network,
service, storage) is P03.06. Emergency-unit/CAD-AVL telemetry is P03.04. Run
manifests, replay and dataset splits are P03.07/P03.08.
