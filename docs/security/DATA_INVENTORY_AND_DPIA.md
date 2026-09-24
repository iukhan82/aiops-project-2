# Data inventory and DPIA-style review

Task P09.06. What data this platform holds, why, how long, who may see it, and what residual privacy risk remains.
Source of truth is `source-code/security/data_inventory.json`; `source-code/security/verify_data_inventory.py` checks every
claim below against the real contracts, `database/retention.py`'s own windows, the UX inventory's real capabilities, and
(when a database is reachable) the live `devices` table of both `aiops` and `aiops_demo` - a category cannot silently
drift from what the platform actually stores or from what is actually running.

This is a DPIA-*style* review for a research and demonstration platform, not a completed Data Protection Impact
Assessment for a production deployment in a named jurisdiction. No legal basis is asserted, no certification is claimed,
and "personal data" below is used descriptively (data about an identifiable person), not as a defined legal term under
any specific law.

## Summary

No special-category data (health, biometric identity, political opinion or similar) is collected anywhere in this
platform. The one path capable in principle of producing identifiable output - an edge camera - is structurally
prevented from doing so before a record ever reaches the platform (P04.06: privacy zones, a non-persistent rehashed id,
a k-anonymity floor). The personal data this platform actually holds is staff identity at username granularity -
operators, dispatchers, supervisors, auditors, demo presenters - kept for safety-critical accountability, together with
the free-text justification an incident commander writes when overriding a command.

## Categories

<!-- BEGIN GENERATED: categories -->
| Category | Personal data | Privacy | Retention | Access | Residual risk |
|---|---|---|---|---|---|
| Roadway and vulnerable-road-user sensor telemetry | no | none | standard | map.view, analytics.view | low |
| Signal, sign and environmental device state | no | none | standard | map.view, analytics.view, commands.request, commands.review | low |
| Edge camera / LiDAR aggregate track metadata | no | aggregated | standard | map.view, analytics.view | medium |
| Operator, dispatcher and service account identity | yes | pseudonymous | audit | audit.view, audit.export | low |
| Emergency call and unit assignment records | no | none | standard | emergency.view, emergency.dispatch | low |
| Policy decisions, command history and override justifications | yes | pseudonymous | audit | audit.view, audit.export, commands.view | low |
| Demo identities and scenario-control runs | yes | pseudonymous | audit | demo.control | low |
<!-- END GENERATED: categories -->

Full detail (purpose, minimization, the data subject, the rights process, and why the residual risk is scored as it is)
for each category is in `source-code/security/data_inventory.json`, read alongside this table rather than repeated here
twice - the JSON is what the verifier checks, so it is the copy that cannot go stale.

## Retention

<!-- BEGIN GENERATED: retention -->
| Class | Window | Used by | Note |
|---|---|---|---|
| `short` | 7 days | not currently assigned to any category above | reserved for the highest-volume, shortest-lived data the schema allows; nothing in the current device population needs a window this short. |
| `standard` | 90 days | traffic-sensor-telemetry, signal-and-field-device-state, edge-vision-aggregate-metadata, emergency-call-and-unit-coordination | the default operational window: long enough for trend analytics and incident review, short enough to bound what is kept. |
| `extended` | 365 days | not currently assigned to any category above | reserved for a data class needing a year for seasonal comparison; none of the current live data uses it. |
| `audit` | never purged | operator-and-service-identity, policy-and-command-decision-records, demo-identities-and-scenario-runs | never purged (`database/retention.py` refuses to touch it) and, since P09.05, hash-chained and append-only - an ACCEPTED design decision: safety-critical accountability outweighs minimization for this narrow set of pseudonymous (username-level) records, and nothing in this class is public-facing personal data. |
<!-- END GENERATED: retention -->

`short`/`standard`/`extended` are enforced by `database/retention.py` (P05.10): a finite window, then the raw row is
folded into a per-device/per-day rollup and deleted - the trend survives, the raw reading does not. `audit` is refused
purge entirely by that module, and, since P09.05, its three tables (plus `retention_runs` itself) are hash-chained and
append-only at the database level, not merely by convention.

## Device lifecycle (CTL-39)

A device's identity is a per-device client certificate issued against the platform's own CA (`infra/platform/mosquitto/
gen-certs.sh`, CTL-09); the broker's ACL (`mosquitto/acl.conf`) restricts a device to its own topics by that identity,
proven by `tests/test_gateway.py` refusing an untrusted-CA and a cross-device certificate.

- **Registration**: a device is created in `devices` with `status = 'active'` and its own certificate; ingestion
  (`backend/ingestion/ingest.py`) refuses an event from a device that is not registered (`REJECTED_UNKNOWN_DEVICE`) -
  a device cannot report before it exists in the identity store.
- **Rotation**: `gen-certs.sh` can reissue a device's certificate against the same CA; the device row's identity does
  not change, only the credential does. This is a documented procedure, not yet an automated schedule.
- **Maintenance / decommission**: `devices.status` (`active`/`inactive`/`maintenance`/`decommissioned`) records the
  device's own lifecycle state; `backend/api/routes_map.py` and the analytics/status views already treat a non-active
  device's data as what it is (stale or absent), never as live.
- **Revocation - not yet enforced.** Removing a device's ability to publish today means changing its `status` at the
  platform layer and, separately, regenerating the broker's trusted-CA material; there is no certificate revocation
  list or OCSP-equivalent check at the broker, so a leaked or decommissioned device's certificate would still
  authenticate to Mosquitto until the CA itself is rotated. This is CTL-39's open gap, tracked as P09.06's own follow-up
  (target: a CRL or short-lived-certificate scheme once a real target host makes broker configuration exercise-able
  end to end, not just in the dev-profile compose stack).

## Rights and residual risk, read together

No category in this inventory needs an access/erasure request process for a member of the public: traffic and
environmental telemetry is never personal, and the one path that could carry personal data (imagery) never reaches the
platform in individually identifiable form. The audit-class categories (operator identity, policy/command decisions,
demo identities) are staff data kept for accountability; they are deliberately never purged, which is the opposite of
data minimization by design, and that trade-off is recorded here as an accepted decision, not hidden as an oversight.

## Not claimed

No production personal-data processing is claimed. No jurisdiction's legal basis is asserted. No certification to
GDPR, a similar framework, or any privacy standard is claimed anywhere in this document or elsewhere in the project.
