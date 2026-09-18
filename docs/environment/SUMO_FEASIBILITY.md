# SUMO and map-tool feasibility evidence

Captured 2026-09-18 on the measured WSL2/Docker development route. This proves a
small deterministic headless simulation; it does not prove the P03.01 district,
field accuracy, target-host operation, or GUI support.

## Pinned route

- Official image: `ghcr.io/eclipse-sumo/sumo:v1_27_1`
- Immutable digest: `sha256:87623396d3501ca8d0ac25154e202bc38baa55a31c724e4dfd6ae60f297c6bf2`
- Observed binary: `Eclipse SUMO sumo 1.27.1`
- Map tool: `netgenerate 1.27.1`
- Runtime: Docker 29.7.2 inside Ubuntu 26.04 WSL2
- Upstream documentation: <https://sumo.dlr.de/docs/Developer/Docker.html>
- Upstream package: <https://github.com/eclipse-sumo/sumo/pkgs/container/sumo>

The executable wrapper is
`source-code/simulator/feasibility/run_container.sh`. It generates a small 3x3
signalized grid, fixed-seed routes, three induction loops and a 600-second run.
It executes SUMO twice and extracts simulated vehicle position/speed records.

## Reproduction

From the repository root in Ubuntu/WSL2:

```bash
docker pull ghcr.io/eclipse-sumo/sumo:v1_27_1
bash source-code/simulator/feasibility/run_container.sh
```

The wrapper itself uses the immutable digest, not the mutable version tag. Two
complete wrapper invocations were executed. Both exited 0 and returned identical
results:

| Measure | Observed result |
|---|---|
| Seed | `20260918` |
| Simulation duration | 600 seconds |
| FCD semantic SHA-256, both internal runs | `1e15c6c5e032c17140843325eb5fa7e9475ede1a8cde4d52556c202d4a24875f` |
| Loop semantic SHA-256, both internal runs | `f62e51c1ff73b19d1b172066f071001ea003d9b5e5486509afba83a87079599d` |
| Extracted records | 15,426 per run |
| Telemetry SHA-256 | `248ac60817c3a3998ab65b1af94fe7c277902600b61103f6fadecff97d5b124f` |
| Deterministic replay | `true` |

Semantic XML hashes intentionally exclude SUMO's generated-at comment; it changes
per process although the simulation records are equal. Telemetry contains a stable
run ID, `simulated` classification, simulation timestamp, vehicle/lane identity,
speed in metres/second, and x/y coordinates in metres. Generated output remains
gitignored.

## Result

P01.06 acceptance is met on the supported development route: a digest-pinned
headless SUMO run, programmatic map generation, deterministic replay and telemetry
extraction all execute. Target-native performance and the full 12-intersection,
three-corridor model remain later tasks.
