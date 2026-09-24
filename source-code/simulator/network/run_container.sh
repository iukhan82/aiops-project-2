#!/usr/bin/env bash
set -euo pipefail

IMAGE="ghcr.io/eclipse-sumo/sumo@sha256:87623396d3501ca8d0ac25154e202bc38baa55a31c724e4dfd6ae60f297c6bf2"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

docker run --rm \
  --user "$(id -u):$(id -g)" \
  --volume "${SCRIPT_DIR}:/work" \
  --workdir /work \
  "${IMAGE}" \
  python3 build_and_verify.py
