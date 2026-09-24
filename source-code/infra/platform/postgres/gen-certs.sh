#!/usr/bin/env bash
# P09.04 (CTL-11): a local test CA and a server certificate for aiops-postgres, generated the same way
# infra/platform/mosquitto/gen-certs.sh already does for the broker. Test-only material: not committed
# (git-ignored), regenerated on demand, never used past this dev fixture - see gen-certs.sh's own note there for why
# a hand-rolled dev CA is the right call here rather than a "real" CA for a stack that only ever runs on localhost.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CERT_DIR="${SCRIPT_DIR}/certs"
rm -rf "${CERT_DIR}"
mkdir -p "${CERT_DIR}"
cd "${CERT_DIR}"

openssl req -x509 -newkey rsa:2048 -days 3650 -nodes \
  -keyout ca.key -out ca.crt -subj "/CN=aiops-platform-test-ca" 2>/dev/null

# SAN covers every name a client on this dev stack dials the database by.
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr \
  -subj "/CN=aiops-postgres" 2>/dev/null
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 3650 \
  -extfile <(printf "subjectAltName=DNS:localhost,DNS:aiops-postgres,DNS:postgres,IP:127.0.0.1") \
  2>/dev/null

rm -f server.csr
chmod 644 ca.crt server.crt
chmod 600 ca.key server.key
echo "wrote ${CERT_DIR}/{ca.crt,ca.key,server.crt,server.key}"
