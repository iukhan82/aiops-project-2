# Infrastructure (Phase 05, P05.01)

Containers, Compose and deployment scripts for the platform's stateful
central services. `source-code/database/` holds schema migrations that run
against the PostgreSQL service provisioned here; `source-code/backend/`
holds the services (gateway, ingestion, APIs) that connect to it.

## `platform/` - pinned MQTT, Kafka-compatible stream, PostgreSQL/PostGIS

Local dev/target-shaped Compose stack (`docker-compose.yml`), three services,
each pinned by digest (ADR-0002, ADR-0003) with resource limits from
`docs/environment/RESOURCE_BUDGET.md`:

| Service | Image | CPU | Memory |
|---|---|---:|---:|
| `mqtt-broker` | `eclipse-mosquitto:2.0.18` | 0.15 | 192 MiB |
| `kafka-broker` | `docker.redpanda.com/redpandadata/redpanda:v24.2.18` | 0.75 | 1024 MiB |
| `postgres` | `postgis/postgis:16-3.4` | 0.75 | 1024 MiB |

The MQTT broker's 0.25 CPU / 384 MiB budget line is shared with the P05.03
gateway; mosquitto alone uses 0.15/192Mi here, leaving headroom for the
gateway process.

Mosquitto's only listener (1883) is plain/anonymous. ADR-0002 permits this
specifically because this Compose stack is the "isolated test fixture" the
ADR carves out - it is not the K3s deployment target. P05.02 adds a second,
mTLS-secured listener (8883) with per-device client certificates and topic
ACLs; nothing beyond this local fixture may use the plain listener.

### Reproduction

```bash
cd source-code/infra/platform
cp .env.example .env        # local dev credentials only; edit if desired
bash up.sh                  # docker compose up -d --wait
bash verify.sh              # resource limits + smoke tests + persistence proof
bash down.sh                # stop (keeps data); add --volumes to wipe it
```

`verify.sh` checks, against the real running containers (not assumptions):

1. **Resource limits**: `docker inspect` `NanoCpus`/`Memory` match the
   compose config exactly, for all three containers.
2. **Functional smoke test** (not just "port open"): a real MQTT publish is
   received on the same topic; a real Kafka-compatible topic is created,
   produced to and consumed from; `SELECT postgis_version()` returns a real
   PostGIS build string.
3. **Persistence across restart**: a retained MQTT message, a Kafka topic's
   log, and a PostgreSQL table row are written, the three containers are
   restarted (not just the process - `docker compose restart`), and all
   three are confirmed to survive using the same named Docker volumes.

Evidence: `output/p05_01_evidence.json` (git-ignored; copied to
`docs/evidence/p05_01_platform_provisioning.json`).

## `platform/mosquitto/` - mTLS identity and topic ACLs (P05.02)

A second, secured MQTT listener (8883) sits alongside the plain 1883 dev
listener from P05.01. `gen-certs.sh` (run automatically by `up.sh` if certs
are missing) generates a local test CA, a broker server cert, two device
client certs, and one client cert signed by a *different*, untrusted CA:

- `require_certificate true` + `cafile` (broker only trusts certs signed by
  the local test CA) + `use_identity_as_username true` (the client cert's
  CN becomes the MQTT username, so `device_id` *is* the identity - no
  separate password to manage or leak).
- `acl_file` with `pattern readwrite devices/%u/#`: each device may only
  publish/subscribe under its own topic tree.
- `per_listener_settings true` scopes all of this to 8883 only; 1883 stays
  as the unauthenticated P05.01 dev fixture.

**Empirical finding, worth flagging explicitly**: this mosquitto version's
classic `acl_file` backend returns an optimistic "granted" MQTT SUBACK code
for *any* subscribed topic filter, regardless of the ACL. The actual read
restriction is enforced at message-delivery time, not at SUBACK time - a
denied subscriber's SUBACK looks like success; it simply never receives a
non-owned topic's messages. Verified directly (not inferred): device A
subscribing to device B's topic gets SUBACK "granted", but when device B
publishes on its own topic, A receives nothing. `verify-mtls.sh` checks
delivery, not the SUBACK code, for exactly this reason - a naive test would
have wrongly reported cross-device read access as blocked when it looked
allowed, or missed that it was actually safe.

Four scenarios verified against the real running broker
(`mosquitto/verify-mtls.sh`):

1. Valid device: own-topic publish/subscribe round trip succeeds.
2. Cross-device publish: device A's message into device B's topic is never
   delivered to B.
3. Cross-device subscribe: device A subscribed to device B's topic never
   receives B's legitimately published message.
4. Bad trust: a client certificate signed by an unrelated CA fails the TLS
   handshake before any MQTT CONNECT is possible (`Error: Protocol error`).

```bash
cd source-code/infra/platform
bash mosquitto/gen-certs.sh   # regenerate local test CA/device/untrusted certs
bash mosquitto/verify-mtls.sh
```

Evidence: `output/p05_02_evidence.json` (git-ignored; copied to
`docs/evidence/p05_02_mqtt_mtls_acl.json`). Certs are test-only material,
regenerated on demand, never committed (`certs/` is git-ignored).

## Why Redpanda for "Kafka-compatible"

ADR-0002 requires a Kafka-compatible broker without mandating Apache Kafka
itself. Redpanda speaks the Kafka wire protocol (verified here with `rpk`,
Redpanda's own client, doing a real produce/consume round trip) without a
JVM or ZooKeeper, which fits the 0.75 CPU/1024 MiB budget line more
comfortably than a Kafka+ZooKeeper pair would on this workstation profile.
`--mode dev-container` sizes Redpanda's internal reactor for a
resource-constrained single-node container rather than production hardware.
