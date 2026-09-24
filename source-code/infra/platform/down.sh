#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Stops and removes containers/network but keeps named volumes (data persists
# across down/up). Pass --volumes to also wipe persisted data.
docker compose down "$@"
