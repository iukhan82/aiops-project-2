#!/usr/bin/env bash
# P05.01 acceptance evidence: each pinned service reaches health, a real
# smoke-tested round trip proves the service actually functions (not just
# "port open"), configured resource limits are confirmed via docker inspect,
# and persistence survives a container restart without losing the volume.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"
mkdir -p output

if [ ! -f .env ]; then
  echo ".env missing; copy .env.example to .env first." >&2
  exit 1
fi
set -a; source .env; set +a

PASS=true
note() { echo ">> $*" >&2; }
fail() { echo "FAIL: $*" >&2; PASS=false; }

note "Bringing stack up and waiting for health..."
docker compose up -d --wait --wait-timeout 120

# ---- resource limits (docker inspect ground truth vs compose config) ----
declare -A EXPECT_NANO_CPUS=( [aiops-mqtt-broker]=150000000 [aiops-kafka-broker]=750000000 [aiops-postgres]=750000000 )
declare -A EXPECT_MEM_BYTES=( [aiops-mqtt-broker]=201326592 [aiops-kafka-broker]=1073741824 [aiops-postgres]=1073741824 )
RESOURCE_JSON="["
first=true
for c in aiops-mqtt-broker aiops-kafka-broker aiops-postgres; do
  nano=$(docker inspect "$c" --format '{{.HostConfig.NanoCpus}}')
  mem=$(docker inspect "$c" --format '{{.HostConfig.Memory}}')
  ok=true
  [ "$nano" = "${EXPECT_NANO_CPUS[$c]}" ] || { fail "$c NanoCpus=$nano want ${EXPECT_NANO_CPUS[$c]}"; ok=false; }
  [ "$mem" = "${EXPECT_MEM_BYTES[$c]}" ] || { fail "$c Memory=$mem want ${EXPECT_MEM_BYTES[$c]}"; ok=false; }
  $ok && note "$c: resource limits match compose config (cpus=$nano/1e9, mem=$mem bytes)"
  $first || RESOURCE_JSON+=","; first=false
  RESOURCE_JSON+="{\"container\":\"$c\",\"nano_cpus\":$nano,\"memory_bytes\":$mem,\"matches_config\":$ok}"
done
RESOURCE_JSON+="]"

# ---- MQTT smoke test: publish then receive on the same topic ----
note "MQTT smoke test..."
MQTT_MSG="p05.01-$(date +%s)"
docker exec aiops-mqtt-broker sh -c "mosquitto_sub -h 127.0.0.1 -t p05/smoketest -C 1 -W 5 > /tmp/mqtt_recv.txt &
sleep 1
mosquitto_pub -h 127.0.0.1 -t p05/smoketest -m '${MQTT_MSG}'
wait"
MQTT_RECV=$(docker exec aiops-mqtt-broker cat /tmp/mqtt_recv.txt || echo "")
if [ "${MQTT_RECV}" = "${MQTT_MSG}" ]; then
  note "MQTT round trip OK: ${MQTT_RECV}"
  MQTT_OK=true
else
  fail "MQTT round trip: sent '${MQTT_MSG}' got '${MQTT_RECV}'"
  MQTT_OK=false
fi

# ---- Kafka-compatible smoke test: create topic, produce, consume ----
note "Kafka-compatible (redpanda) smoke test..."
docker exec aiops-kafka-broker rpk topic delete p05-smoketest >/dev/null 2>&1 || true
docker exec aiops-kafka-broker rpk topic create p05-smoketest -p 1 -r 1 >/dev/null
KAFKA_MSG="p05.01-$(date +%s)"
echo "${KAFKA_MSG}" | docker exec -i aiops-kafka-broker rpk topic produce p05-smoketest >/dev/null
KAFKA_RECV=$(docker exec aiops-kafka-broker rpk topic consume p05-smoketest -n 1 -o start --format '%v\n' 2>/dev/null || echo "")
if [ "${KAFKA_RECV}" = "${KAFKA_MSG}" ]; then
  note "Kafka round trip OK: ${KAFKA_RECV}"
  KAFKA_OK=true
else
  fail "Kafka round trip: sent '${KAFKA_MSG}' got '${KAFKA_RECV}'"
  KAFKA_OK=false
fi

# ---- PostgreSQL/PostGIS smoke test ----
note "PostgreSQL/PostGIS smoke test..."
PGIS_VERSION=$(docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" aiops-postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc "SELECT postgis_version();" | xargs)
if [ -n "${PGIS_VERSION}" ]; then
  note "PostGIS active: ${PGIS_VERSION}"
  PG_OK=true
else
  fail "postgis_version() returned empty"
  PG_OK=false
fi

# ---- Persistence: write markers, restart containers, confirm survival ----
note "Persistence check: writing markers, restarting containers..."
docker exec aiops-mqtt-broker sh -c "mosquitto_pub -h 127.0.0.1 -t p05/retained -r -m 'persisted-${MQTT_MSG}'"
docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" aiops-postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c \
  "CREATE TABLE IF NOT EXISTS platform_provisioning_check (id serial PRIMARY KEY, marker text, created_at timestamptz DEFAULT now()); INSERT INTO platform_provisioning_check (marker) VALUES ('${MQTT_MSG}');" >/dev/null

docker compose restart mqtt-broker kafka-broker postgres >/dev/null
docker compose up -d --wait --wait-timeout 120 >/dev/null

MQTT_PERSIST=$(docker exec aiops-mqtt-broker sh -c "mosquitto_sub -h 127.0.0.1 -t p05/retained -C 1 -W 5")
KAFKA_PERSIST=$(docker exec aiops-kafka-broker rpk topic consume p05-smoketest -n 1 -o start --format '%v\n' 2>/dev/null || echo "")
PG_PERSIST=$(docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" aiops-postgres psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc \
  "SELECT marker FROM platform_provisioning_check WHERE marker = '${MQTT_MSG}';" | tr -d '[:space:]')

MQTT_PERSIST_OK=false; [ "${MQTT_PERSIST}" = "persisted-${MQTT_MSG}" ] && MQTT_PERSIST_OK=true
KAFKA_PERSIST_OK=false; [ "${KAFKA_PERSIST}" = "${KAFKA_MSG}" ] && KAFKA_PERSIST_OK=true
PG_PERSIST_OK=false; [ "${PG_PERSIST}" = "${MQTT_MSG}" ] && PG_PERSIST_OK=true

$MQTT_PERSIST_OK && note "MQTT persistence OK (retained message survived restart)" || fail "MQTT persistence: got '${MQTT_PERSIST}'"
$KAFKA_PERSIST_OK && note "Kafka persistence OK (topic log survived restart)" || fail "Kafka persistence: got '${KAFKA_PERSIST}'"
$PG_PERSIST_OK && note "PostgreSQL persistence OK (row survived restart)" || fail "PostgreSQL persistence: got '${PG_PERSIST}'"

cat > output/p05_01_evidence.json <<EOF
{
  "task": "P05.01",
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "images": {
    "mqtt_broker": "eclipse-mosquitto:2.0.18@sha256:d12c8f80dfc65b768bb9acecc7ef182b976f71fb681640b66358e5e0cf94e9e9",
    "kafka_broker": "docker.redpanda.com/redpandadata/redpanda:v24.2.18@sha256:0270951e8793c77f0dd9178d137198018fa7ffd920e5a2a3b02a050ec76195b8",
    "postgres": "postgis/postgis:16-3.4@sha256:44126d872ac91993766c341e369c539e8196614321765d36a6f1bab0419a5fa5"
  },
  "resource_limits": ${RESOURCE_JSON},
  "smoke_tests": {
    "mqtt_round_trip": ${MQTT_OK},
    "kafka_round_trip": ${KAFKA_OK},
    "postgis_active": ${PG_OK},
    "postgis_version": "${PGIS_VERSION}"
  },
  "persistence_after_restart": {
    "mqtt_retained_message_survived": ${MQTT_PERSIST_OK},
    "kafka_topic_log_survived": ${KAFKA_PERSIST_OK},
    "postgres_row_survived": ${PG_PERSIST_OK}
  },
  "all_passed": $PASS
}
EOF
note "Evidence written to output/p05_01_evidence.json"

if [ "$PASS" = true ]; then
  note "P05.01: ALL CHECKS PASSED"
  exit 0
else
  note "P05.01: ONE OR MORE CHECKS FAILED"
  exit 1
fi
