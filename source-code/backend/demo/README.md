# Demo world

What the operator UI shows when nothing real is connected: a separate database (`aiops_demo`, never the `aiops` one the verify scripts
write to and clean) fed by recorded simulator streams re-timed to the wall clock, so screens have current, changing data. Nothing here
creates an incident, a recommendation or a KPI by hand: the platform's own detectors, incident correlator, KPI computation, forecast
package and recommendation generator run over what arrives. Every event keeps its `simulated` truth label.

| File | What it does |
|---|---|
| `world.py` | creates (or, with `--reset`, recreates) the demo database, migrates it, seeds topology and segments, registers the 76 recorded devices |
| `feeder.py` | replays the recorded streams and runs the analysis once a minute (see below); writes a `demo-feeder` heartbeat |
| `seed_actions.py` | gives the world a command history covering every state a person will meet; real and recorded histories are told apart in the data |
| `fixtures.py` | small real-path fixtures for the browser tests (an incident, a unit position, a detector count, an injected policy outage, an API call as a named person, scenario-control runs) |

## The feeder

```sh
python source-code/backend/demo/feeder.py --reset --start-offset-min 60
```

- A 3-hour `am-peak-blockage` loop-detector run (P06 dataset), the recorded CAD export and AVL telemetry of the emergency scenario
  (P03.04), every event re-timed by one constant offset so "minute N of the run" happens at start + N minutes. The run loops (new event
  ids each time), so a demo can run all day. `--start-offset-min` says how far into the run "now" is; the first recorded blockages
  are at minutes 53 and 59 and a third at 110.
- The recorded 10-minute sensor stream (signal phase and timing, weather, road condition, pedestrian and cycle detectors) repeats end to end.
- Once a minute, on a second connection, the analysis runs over what has arrived.
- `--reset` drops and rebuilds the database first, then backfills history (about 90 s), so a fresh world already has incidents.

**Honest limits, all visible in the UI:** the emergency CAD/AVL stream is finite within the loop, so its devices go stale between
replays and the platform-status screen says so; replayed traffic does not react to commands, so the verifier's measurements of a
command executed here carry the note "the replayed traffic in the demo world does not react to commands" and their classification
reflects the recorded traffic, not the command.

## Command histories (`seed_actions.py`)

Needs the API, the feeder, the command executor and (for outcomes) the outcome verifier running.

- **Real**, made now through the running API as named demo people with real tokens: a command left waiting for approval, one denied with
  a reason, one whose approval was refused by an injected policy outage (fault injection), and two the running executor really executes in
  the SUMO container (a sign message and a signal extension); a command whose target the adapter cannot find, so the executor really
  fails it; one nobody approved in time, so it really expires.
- **Recorded**: four completed histories, one per outcome class (effective, ineffective, unsafe with its physical undo, unknown), built
  with the measured numbers of P07.09's lock-step simulator runs (`docs/evidence/p07_09_outcomes.json`). Their state history and outcome
  detail say they were not executed in this session, and each is classified by the same `verify_and_rollback` the verifier uses.

Time-limited seeds (a waiting command has a 5-minute request TTL) decay; the browser tests create what they need through `fixtures.py`
instead of relying on them.

## Fixtures

```sh
python source-code/backend/demo/fixtures.py incident --severity high
python source-code/backend/demo/fixtures.py unit-position --unit ambulance-1 --intersection int-b4 [--age-minutes 30 --reset]
python source-code/backend/demo/fixtures.py loop-event --device loop-int-a1-int-a2 --count 57
python source-code/backend/demo/fixtures.py policy-outage
python source-code/backend/demo/fixtures.py probe-device [--id probe-loop-latency]   # a detector nothing in the replay drives, for timing a reading to the screen
python source-code/backend/demo/fixtures.py as-user --user fin.hassan --method POST --path /api/v1/commands --json '{...}'
python source-code/backend/demo/fixtures.py demo-running --count 2      # and: demo-clear
```

Each prints one JSON object. On Windows under git-bash set `MSYS_NO_PATHCONV=1` for `--path`, or the shell rewrites it.
