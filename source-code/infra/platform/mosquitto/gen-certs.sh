#!/usr/bin/env bash
# Generates a local test CA, a broker server cert, two device client certs
# and one "untrusted" client cert (signed by a *different* CA) used to prove
# ADR-0002/P05.02's bad-trust rejection. Test-only material: not committed
# (git-ignored), regenerated on demand, never used past this dev fixture.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CERT_DIR="${SCRIPT_DIR}/certs"
rm -rf "${CERT_DIR}"
mkdir -p "${CERT_DIR}"
cd "${CERT_DIR}"

DEVICE_A="corridor-a-int-03-loop-01"
DEVICE_B="corridor-a-int-03-loop-02"
GATEWAY_CN="gateway"

# --- trusted CA ---
openssl req -x509 -newkey rsa:2048 -days 3650 -nodes \
  -keyout ca.key -out ca.crt -subj "/CN=aiops-platform-test-ca" 2>/dev/null

# --- broker server cert (SAN covers every name the verify script may dial) ---
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr \
  -subj "/CN=aiops-mqtt-broker" 2>/dev/null
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 3650 \
  -extfile <(printf "subjectAltName=DNS:localhost,DNS:aiops-mqtt-broker,DNS:mqtt-broker,IP:127.0.0.1") \
  2>/dev/null

# --- two trusted device client certs (CN = device_id, matches use_identity_as_username) ---
for dev in "${DEVICE_A}" "${DEVICE_B}"; do
  openssl req -newkey rsa:2048 -nodes -keyout "device-${dev}.key" -out "device-${dev}.csr" \
    -subj "/CN=${dev}" 2>/dev/null
  openssl x509 -req -in "device-${dev}.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out "device-${dev}.crt" -days 3650 2>/dev/null
  rm -f "device-${dev}.csr"
done

# --- gateway client cert (P05.03: broad read/write per acl.conf's "user gateway" stanza) ---
openssl req -newkey rsa:2048 -nodes -keyout "gateway.key" -out "gateway.csr" \
  -subj "/CN=${GATEWAY_CN}" 2>/dev/null
openssl x509 -req -in "gateway.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out "gateway.crt" -days 3650 2>/dev/null
rm -f gateway.csr

# --- untrusted CA + client, for the bad-trust rejection proof ---
openssl req -x509 -newkey rsa:2048 -days 3650 -nodes \
  -keyout untrusted-ca.key -out untrusted-ca.crt -subj "/CN=untrusted-outside-ca" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -keyout untrusted-client.key -out untrusted-client.csr \
  -subj "/CN=rogue-device" 2>/dev/null
openssl x509 -req -in untrusted-client.csr -CA untrusted-ca.crt -CAkey untrusted-ca.key \
  -CAcreateserial -out untrusted-client.crt -days 3650 2>/dev/null
rm -f untrusted-client.csr server.csr *.srl

chmod 644 *.crt *.key
echo "Certs written to ${CERT_DIR}"
echo "Device A CN: ${DEVICE_A}"
echo "Device B CN: ${DEVICE_B}"
