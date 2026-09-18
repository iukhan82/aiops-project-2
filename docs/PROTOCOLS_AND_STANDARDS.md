# Protocols and standards baseline

This is an implementation baseline, not a claim of certification or conformance.
Each protocol must have a version, trust boundary, authentication, failure mode,
timeout, retry, ordering and evidence plan before use.

## Mandatory technology protocols from the assessment

| Area | Baseline use |
|---|---|
| MQTT | Edge telemetry and command acknowledgement, QoS 1, per-device identity, mTLS, topic ACLs, expiry and deduplication |
| Kafka-compatible streaming | Central event backbone, partition keys and schemas; Redpanda is acceptable only when Kafka protocol compatibility is documented |
| HTTPS REST | Management and staff APIs with OIDC access tokens, validation, quotas and audit |
| WebSocket or SSE | Authenticated operator updates with reconnect, cursor and stale-state behavior |
| OpenTelemetry OTLP | Traces, metrics and logs over authenticated HTTP/gRPC where available |
| PostgreSQL wire/TLS | Application persistence with scoped roles and migrations |
| OIDC/OAuth 2.0 | Keycloak authentication and scoped API authorization; PKCE for browser clients |

## Traffic and emergency interoperability candidates

These adapters are optional until a real integration is in scope. Record the exact
profile/version before implementation.

- NTCIP for traffic controllers and variable message signs.
- SAE J2735 or applicable regional V2X messages for SPaT/MAP and vehicle events.
- GTFS Realtime for transit vehicle positions, trip updates and alerts.
- GeoJSON plus versioned road/lane identifiers for internal geospatial APIs.
- DATEX II where European traffic-information exchange is required.
- Common Alerting Protocol (CAP) for compatible public-warning adapters.
- Vendor-neutral CAD/AVL abstraction; never imply compatibility with a specific
  emergency service without a tested adapter and authorization.

## Security and compliance references

- GDPR Regulation (EU) 2016/679: lawful/purpose-limited processing, minimization,
  accuracy, retention, integrity/confidentiality, accountability, privacy by
  design/default, rights handling and DPIA where high risk.
- ISO/IEC 27001:2022 and relevant ISO/IEC 27002 controls: use an ISMS-style scope,
  risks, assets, owners, treatment, evidence and continual improvement. Do not
  claim certification.
- NIST Cybersecurity Framework 2.0: Govern, Identify, Protect, Detect, Respond,
  Recover.
- NIST IR 8259A: device identification, configuration, data protection, logical
  access, secure software update, and cybersecurity-state awareness.
- NIST SP 800-82 Rev. 3: OT safety, reliability, availability, zones/conduits,
  change control and secure operations guidance.
- ETSI EN 303 645 V3.1.3: selected IoT baseline outcomes; applicability is mapped
  because traffic/OT devices are not necessarily consumer IoT products.
- IEC 62443 principles: zones/conduits, system risk assessment, security levels,
  component/system lifecycle and least privilege. Licensed standard text is not
  reproduced and conformance is not claimed.
- OWASP ASVS 5.0 selected Level 2 requirements for the staff web application/API.
- NIST AI RMF for model validity, transparency, monitoring and risk treatment.
- NIST SSDF, SBOM and signed-build provenance concepts for software supply chain.

## Authoritative reference links

- GDPR: https://eur-lex.europa.eu/eli/reg/2016/679/oj
- ISO/IEC 27001:2022: https://www.iso.org/standard/27001
- NIST CSF 2.0: https://www.nist.gov/cyberframework
- NIST IR 8259A: https://csrc.nist.gov/pubs/ir/8259/a/final
- NIST SP 800-82 Rev. 3: https://csrc.nist.gov/pubs/sp/800/82/r3/final
- ETSI EN 303 645: https://www.etsi.org/standards
- OWASP ASVS: https://owasp.org/projects/asvs
