#!/usr/bin/env bash
set -euo pipefail

IMAGE="ghcr.io/eclipse-sumo/sumo@sha256:87623396d3501ca8d0ac25154e202bc38baa55a31c724e4dfd6ae60f297c6bf2"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SIMULATOR_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

if [ ! -f "${SIMULATOR_DIR}/network/output/district.net.xml" ]; then
  echo "Run source-code/simulator/network/run_container.sh first (P03.01 network build)." >&2
  exit 1
fi

docker run --rm \
  --user "$(id -u):$(id -g)" \
  --volume "${SIMULATOR_DIR}:/work" \
  --workdir /work/emergency \
  "${IMAGE}" \
  python3 build_and_verify.py

echo
echo "Now validate schema conformance on the host venv:" >&2
echo "  python -m pytest source-code/tests/test_emergency_contracts.py -q" >&2
