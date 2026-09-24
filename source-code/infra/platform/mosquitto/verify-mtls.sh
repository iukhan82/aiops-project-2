#!/usr/bin/env bash
# P05.02 acceptance evidence: a valid device works; cross-device publish and
# subscribe both fail; a client whose certificate is not signed by the
# trusted CA fails before any MQTT CONNECT is possible.
#
# Ground truth is message DELIVERY, not the MQTT SUBACK return code: this
# broker's acl_file backend grants SUBACK optimistically for any topic
# filter and enforces the actual read restriction at delivery time (verified
# empirically; see source-code/infra/README.md). A test that only checked
# SUBACK would wrongly report cross-device read as allowed.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
mkdir -p "${PLATFORM_DIR}/output"

DEV_A="corridor-a-int-03-loop-01"
DEV_B="corridor-a-int-03-loop-02"
C="/mosquitto/certs"
PASS=true
note() { echo ">> $*" >&2; }
fail() { echo "FAIL: $*" >&2; PASS=false; }

note "Valid device: own pub/sub round trip..."
MSG="p05.02-$(date +%s)"
docker exec aiops-mqtt-broker sh -c "
  mosquitto_sub -h localhost -p 8883 --cafile ${C}/ca.crt --cert ${C}/device-${DEV_A}.crt --key ${C}/device-${DEV_A}.key -t devices/${DEV_A}/telemetry -C 1 -W 5 > /tmp/own.txt &
  sleep 1
  mosquitto_pub -h localhost -p 8883 --cafile ${C}/ca.crt --cert ${C}/device-${DEV_A}.crt --key ${C}/device-${DEV_A}.key -t devices/${DEV_A}/telemetry -m '${MSG}'
  wait
"
OWN_RECV=$(docker exec aiops-mqtt-broker cat /tmp/own.txt)
VALID_OK=false
if [ "${OWN_RECV}" = "${MSG}" ]; then note "valid device OK: ${OWN_RECV}"; VALID_OK=true; else fail "valid device: got '${OWN_RECV}'"; fi

note "Cross-device publish: A writes into B's topic, B must not receive it..."
docker exec aiops-mqtt-broker sh -c "
  mosquitto_sub -h localhost -p 8883 --cafile ${C}/ca.crt --cert ${C}/device-${DEV_B}.crt --key ${C}/device-${DEV_B}.key -t devices/${DEV_B}/telemetry -C 1 -W 4 > /tmp/cross_write.txt &
  sleep 1
  mosquitto_pub -h localhost -p 8883 --cafile ${C}/ca.crt --cert ${C}/device-${DEV_A}.crt --key ${C}/device-${DEV_A}.key -t devices/${DEV_B}/telemetry -m cross-write-attempt
  wait
" || true
CROSS_WRITE_RECV=$(docker exec aiops-mqtt-broker cat /tmp/cross_write.txt)
CROSS_WRITE_BLOCKED=true
if [ -n "${CROSS_WRITE_RECV}" ]; then fail "cross-device publish was NOT blocked: B received '${CROSS_WRITE_RECV}'"; CROSS_WRITE_BLOCKED=false
else note "cross-device publish correctly blocked (B received nothing)"; fi

note "Cross-device subscribe: A subscribes to B's topic, must not see B's own publish..."
docker exec aiops-mqtt-broker sh -c "
  mosquitto_sub -h localhost -p 8883 --cafile ${C}/ca.crt --cert ${C}/device-${DEV_A}.crt --key ${C}/device-${DEV_A}.key -t devices/${DEV_B}/telemetry -C 1 -W 4 > /tmp/cross_read.txt &
  sleep 1
  mosquitto_pub -h localhost -p 8883 --cafile ${C}/ca.crt --cert ${C}/device-${DEV_B}.crt --key ${C}/device-${DEV_B}.key -t devices/${DEV_B}/telemetry -m legit-from-B
  wait
" || true
CROSS_READ_RECV=$(docker exec aiops-mqtt-broker cat /tmp/cross_read.txt)
CROSS_READ_BLOCKED=true
if [ -n "${CROSS_READ_RECV}" ]; then fail "cross-device subscribe was NOT blocked: A received '${CROSS_READ_RECV}'"; CROSS_READ_BLOCKED=false
else note "cross-device subscribe correctly blocked (A received nothing)"; fi

note "Bad trust: client cert signed by an untrusted CA must fail the TLS handshake..."
set +e
BAD_TRUST_OUT=$(docker exec aiops-mqtt-broker sh -c "mosquitto_pub -h localhost -p 8883 --cafile ${C}/ca.crt --cert ${C}/untrusted-client.crt --key ${C}/untrusted-client.key -t devices/rogue-device/telemetry -m should-not-connect" 2>&1)
BAD_TRUST_EXIT=$?
set -e
BAD_TRUST_REJECTED=false
if [ "${BAD_TRUST_EXIT}" -ne 0 ]; then note "bad-trust cert correctly rejected: ${BAD_TRUST_OUT}"; BAD_TRUST_REJECTED=true
else fail "bad-trust cert was accepted (exit 0): ${BAD_TRUST_OUT}"; fi

cat > "${PLATFORM_DIR}/output/p05_02_evidence.json" <<EOF
{
  "task": "P05.02",
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "mechanism": {
    "listener": "8883",
    "auth": "mTLS, require_certificate + use_identity_as_username (client cert CN becomes MQTT username)",
    "authorization": "mosquitto acl_file, pattern readwrite devices/%u/#"
  },
  "note": "SUBACK return code is granted optimistically by this broker version for any topic filter; enforcement is verified at message delivery time, not via the SUBACK code.",
  "tests": {
    "valid_device_own_topic_round_trip": ${VALID_OK},
    "cross_device_publish_blocked": ${CROSS_WRITE_BLOCKED},
    "cross_device_subscribe_blocked": ${CROSS_READ_BLOCKED},
    "untrusted_ca_cert_rejected": ${BAD_TRUST_REJECTED}
  },
  "all_passed": $PASS
}
EOF
note "Evidence written to output/p05_02_evidence.json"

if [ "$PASS" = true ]; then
  note "P05.02: ALL CHECKS PASSED"
  exit 0
else
  note "P05.02: ONE OR MORE CHECKS FAILED"
  exit 1
fi
