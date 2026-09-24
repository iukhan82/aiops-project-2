#!/usr/bin/env bash
# P04.09: run the edge runtime under its real ADR-0006 resource budget
# (0.75 CPU / 512 MiB), non-root, read-only rootfs, replaying the held-out
# test split's real telemetry (source-code/models/evaluation/prepare_benchmark_run.py
# must have already produced output/benchmark_events.jsonl + config).
set -euo pipefail

IMAGE="edge-runtime:p04"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
OUT_DIR="${SCRIPT_DIR}/output"

if [ ! -f "${OUT_DIR}/benchmark_events.jsonl" ]; then
  echo "Run prepare_benchmark_run.py first." >&2
  exit 1
fi
if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  docker build -f "${SOURCE_DIR}/edge/Dockerfile" -t "${IMAGE}" "${SOURCE_DIR}"
fi

docker run --rm \
  --user 10001:10001 \
  --cpus=0.75 --memory=512m --memory-swap=512m --pids-limit=64 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --read-only --tmpfs /tmp \
  -v "${OUT_DIR}/devices.jsonl:/data/devices.jsonl:ro" \
  -v "${SOURCE_DIR}/models/registry:/data/registry:ro" \
  -v "${OUT_DIR}/benchmark_config.json:/data/config.json:ro" \
  -v "${OUT_DIR}/benchmark_events.jsonl:/data/events.jsonl:ro" \
  -v "${OUT_DIR}:/out" \
  "${IMAGE}" replay --config /data/config.json --events /data/events.jsonl \
  --capture-latency --report /out/container_report.json

echo "Wrote ${OUT_DIR}/container_report.json" >&2
