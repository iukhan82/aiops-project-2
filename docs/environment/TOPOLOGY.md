# Environment topology decision

Status: accepted baseline for planning; revisit after target inventory and measured
load tests.

## Development profile

- Windows 11 hosts the editor and repository.
- WSL2 Ubuntu 26.04 hosts Python/Linux tools and the active Docker Engine.
- Docker Compose runs project-scoped dependencies and services.
- SUMO runs headless inside WSL or a pinned container. SUMO GUI is optional and is
  not required for automated tests.
- Edge sites are three logical corridor/intersection groups, each with separate
  identity, configuration and durable outbox. They share one development machine
  but never claim physical separation.
- MQTT handles edge uplink. A Kafka-compatible broker handles central streams.
- PostgreSQL/PostGIS stores authoritative geometry and control records.
- Keycloak, OPA and the observability stack run only when needed by the selected
  development profile.
- Databases and brokers use WSL-native volumes. Source remains on the D: workspace
  unless measured filesystem behavior requires a WSL-native working copy.

Development proves code and local integration. It does not prove target K3s,
Falco, remote cloud, availability or physical-edge performance.

## Assessment profile

Required target: dedicated Ubuntu LTS host with native cgroup v2, Docker/containerd
and K3s. Minimum planning capacity is 8 logical CPUs, 16 GiB RAM and 100 GiB free
SSD. Recommended capacity is 12 logical CPUs, 32 GiB RAM and 200 GiB free SSD.

The target runs:

- K3s control plane and project namespace.
- Three logical edge deployments with independent identities/PVC outboxes.
- Mosquitto, Kafka-compatible broker, ingestion, state, traffic, incident,
  emergency, optimization, command and outcome services.
- PostgreSQL/PostGIS, Keycloak, OPA and operator frontend.
- OpenTelemetry Collector, Prometheus, Grafana, Tempo and bounded logs.
- Falco on the node plus project-scoped runtime rules.

This single-node assessment topology demonstrates real orchestration, isolation,
security and recovery. It is not production high availability and not proof of
geographically distributed edge sites.

## Proposed production profile

- Intersection/corridor edge nodes retain safe local signal plans, inference and
  durable buffering.
- Redundant regional gateways terminate device trust and bridge into a multi-zone
  central event backbone.
- Central services, databases, identity, policy and observability use separate
  failure zones and managed backup/restore.
- Controller, CAD/AVL, transit, weather and public-alert adapters occupy explicit
  zones/conduits with allowlisted protocols and independent credentials.
- Public/operator endpoints use HTTPS, WAF/rate limits and protected administrative
  access. Private service paths use workload identity and network policy.
- Disaster recovery has a tested secondary location and explicit RPO/RTO.

This is design scope only. No production/cloud deployment is claimed.

## Network exposure

- Development ports bind to loopback unless LAN access is explicitly needed.
- MQTT uses 8883 with mTLS. Plain 1883 is disabled outside isolated test fixtures.
- Kafka-compatible transport remains internal.
- Operator UI/API/Keycloak/Grafana use authenticated endpoints; assessment HTTP
  exceptions, if any, remain local/LAN-only and documented.
- Databases, OPA, telemetry receivers and management endpoints are not public.

## Decision rationale

WSL2 Docker already works and fits local iteration. Compose reduces overhead on an
11 GiB WSL allocation. Native Linux K3s remains required because WSL nested-cluster
and runtime-security behavior can differ from supported deployment. Proposed
production separates edge, gateways and central zones without pretending the
academic target is highly available.
