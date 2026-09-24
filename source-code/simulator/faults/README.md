# P03.06: device/platform fault scenarios with separate ground truth

Seven required fault types (silence, stuck, clock, network, model, service,
storage), each with a ground-truth record kept separate from the telemetry:
`scenario_id`, `scenario_type`, `onset`, `end`, `affected_entities`,
`description`, `truth_label`, `synthetic_overlay` - same shape as P03.05's
`ground_truth.jsonl`, a plain internal JSON structure, not a new formal
contract.

## Pure Python - no SUMO, no container

Every one of these seven is a platform/AIOps-layer fault (device health,
clock sync, network link, model/service/storage health), not road physics.
SUMO has nothing to do with any of them, so unlike P03.01-P03.05 this stage
needs no `run_container.sh` - just:

```bash
python source-code/simulator/faults/build_and_verify.py
python -m pytest source-code/tests/test_fault_ground_truth.py -q
```

It only reads the already-built
`source-code/simulator/network/output/district.net.xml` (P03.01) to place
four of the faults on real P03.03 catalog device locations.

## Mechanism (`generate_fault_events.py`)

Same two-event overlay pattern as P03.05's overlays: an onset
(`observation-envelope/v1` event with a degraded `quality` or
`clock_quality: "drifting"`) and an end (recovered).

| Fault | Device | What changes at onset |
|---|---|---|
| `silence` | `cycle_counter` | `seconds_since_last_seen: 60`, `quality: "invalid"` |
| `stuck` | `inductive_loop` | `stuck_value_flag: true`, same `repeated_value` for the whole window |
| `clock` | `weather_station` | `clock_quality: "drifting"` at the envelope level, `clock_offset: 47.5s` |
| `network` | `crossing_detector` | `ingest_time` lags `observation_time` by 45s (vs. the usual 0.25s), `packet_loss: 0.22` |
| `model` | new `aiops_agent` device | `confidence_mean` drops to 0.31, `input_drift_score` rises to 0.72 |
| `service` | same `aiops_agent` device | `request_error_rate: 0.38`, `saturation: 0.94` |
| `storage` | same `aiops_agent` device | `consumer_lag: 480s`, `replay_backlog: 12000` |

`model`/`service`/`storage` have no device in the P03.03 sensor catalog (it
only covers road-facing sensors), so this stage registers one new
`device/v1` record, `aiops-agent-platform-self-observability`
(`device_type: "aiops_agent"`, already in the contract's enum), representing
the platform's own self-observability agent - not a new road sensor.

## Reproduction and verification

`build_and_verify.py` runs the whole pipeline twice and asserts
`ground_truth.jsonl`, `fault_events.jsonl` and `aiops_agent_device.json` are
byte-identical across runs, and that all 7 required fault types are present.
Contract conformance (`contracts/observation-envelope/v1`,
`contracts/device/v1`) and the ground-truth shape/determinism checks are
`source-code/tests/test_fault_ground_truth.py` (7 tests) - which, because
this stage needs no Docker, can regenerate the output itself if it's
missing rather than only reading pre-generated files.

Last verified run (2026-09-18): 7 faults, 14 events (2 per fault); all
outputs byte-identical across two runs; 7/7 host-side tests passed.

## Scope

`P03.05`'s `signal` scenario is a sensor-*reported* controller fault
(SPaT telemetry showing `signal_state: "fault"`); this stage's faults are
about the *platform's own* observability of its devices/services, a
different layer. Run manifests/replay and dataset splits are
P03.07/P03.08.
