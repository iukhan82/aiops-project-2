#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [ ! -f .env ]; then
  echo ".env missing; copy .env.example to .env first (local dev credentials only)." >&2
  exit 1
fi

if [ ! -f mosquitto/certs/ca.crt ]; then
  echo "Generating local test CA/device/untrusted certs (mosquitto/gen-certs.sh)..." >&2
  bash mosquitto/gen-certs.sh
fi

docker compose up -d --wait --wait-timeout 240

# The policy engine image is distroless (no shell), so compose cannot health-check it. Wait until it has loaded the policy instead:
# every protected API request, approval and execution is refused until it answers.
policy_up=false
for _ in $(seq 1 60); do
  if version=$(curl -sf http://127.0.0.1:8181/v1/data/aiops/model/version) && echo "$version" | grep -q '"result"'; then
    echo "policy engine is serving: $version"
    policy_up=true
    break
  fi
  sleep 1
done
if [ "$policy_up" != true ]; then
  echo "the policy engine did not load its policy within 60 s; protected requests will be refused (503) until it does" >&2
  exit 1
fi
docker compose ps
