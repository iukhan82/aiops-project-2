# P07.06 - simulator signal/diversion/VMS adapters (real TraCI)

`adapters.py` (the three real actions), `run_action.py` (container entrypoint: one action in,
one result out, an idempotency ledger that survives across container runs), `run_container.sh`.
Host side: `source-code/backend/control/simulator_adapters.py`.

Each execution is one fresh, isolated invocation of the pinned SUMO image - the same execution
boundary every SUMO-touching stage of this project uses. The backend writes `output/action.json`,
runs this script through WSL Docker, and reads back `output/result.json`; `output/ledger.jsonl`
(a real file on the host-mounted volume, not container-local storage) is what makes a repeated
`idempotency_key` a genuine no-op - the second invocation never even starts SUMO.

`signal_controller_adapter` extends the traffic light's *current* phase (`traci.trafficlight.
setPhaseDuration`) against a real `actuated` program - every intersection in this network has one
(P03.01) - clamped to the caller's `max_deviation_s` as the adapter's own last check, independent of
whatever P07.04/P07.05 already enforced upstream. `diversion_adapter` closes the segment's real
general-traffic lanes (`traci.lane.setDisallowed`), so "traffic no longer uses this segment" is a
fact SUMO's own routing now enforces, not a hope. `vms_adapter` has **no real SUMO actuation** and
its result says so plainly: a message sign changes human drivers' voluntary choices, which this
simulator does not model.

See `backend/control/README.md`'s P07.06 section for the full acceptance evidence (10 real-Postgres
+ real-Docker + real-TraCI checks) and `docs/evidence/p07_06_simulator_adapters.json`.
