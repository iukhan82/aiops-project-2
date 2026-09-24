#!/usr/bin/env bash
# P07.06: run one simulator-adapter action inside the digest-pinned SUMO
# image. Called by backend/control/simulator_adapters.py once per approved
# command. The caller writes simulator/control_adapters/output/action.json
# *before* invoking this script (that file, result.json and ledger.jsonl all
# live under this real, persistent directory - the idempotency ledger
# survives across container runs because it is a host file, not container
# storage). Usage: run_container.sh (reads output/action.json).
set -euo pipefail

IMAGE="ghcr.io/eclipse-sumo/sumo@sha256:87623396d3501ca8d0ac25154e202bc38baa55a31c724e4dfd6ae60f297c6bf2"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

mkdir -p "${SCRIPT_DIR}/output"

docker run --rm \
  -e PYTHONPATH=/usr/share/sumo/tools \
  --volume "${SOURCE_DIR}:/work" \
  -w /work/simulator/control_adapters \
  "${IMAGE}" \
  python3 run_action.py output/action.json output/result.json output/ledger.jsonl
