#!/usr/bin/env bash
# Build the edge-model dataset (real SUMO runs) inside the digest-pinned SUMO image.
# Optional arg: a single seed (smoke run), e.g. `run_container.sh 20260918`.
set -euo pipefail

IMAGE="ghcr.io/eclipse-sumo/sumo@sha256:87623396d3501ca8d0ac25154e202bc38baa55a31c724e4dfd6ae60f297c6bf2"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

if [ ! -f "${SOURCE_DIR}/simulator/network/output/district.net.xml" ]; then
  echo "Run source-code/simulator/network/run_container.sh first (P03.01 network build)." >&2
  exit 1
fi

docker run --rm \
  --user "$(id -u):$(id -g)" \
  --volume "${SOURCE_DIR}:/work" \
  --workdir /work/models/dataset \
  "${IMAGE}" \
  python3 build_edge_dataset.py "$@"
