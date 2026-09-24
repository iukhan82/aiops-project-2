# ADR-0006: Edge compute runtime

- **Status:** Accepted
- **Date:** 2026-09-18
- **Deciders:** ARCH, with EDGE/DATA as implementing owners

## Context

Each of the three logical edge sites (corridor/intersection groups) must run
protocol adapters, schema validation, privacy-preserving feature extraction,
local inference (ADR-0005), and a durable offline outbox that survives
crash/restart/uplink loss without duplication (P04.01, P04.07). Budget is
0.25 CPU/256 MiB request per edge instance, 0.75 CPU/512 MiB limit
(`docs/environment/RESOURCE_BUDGET.md`).

## Decision drivers

- Must host ONNX Runtime (ADR-0005) with good CPU-bound async I/O for
  concurrent MQTT publish, TraCI/telemetry ingestion, and health reporting.
- Durable outbox needs crash-safe, ordered, exactly-once-by-meaning local
  storage without operating a second database engine per edge site.
- Small resource footprint across three concurrent logical instances on one
  development workstation.
- Team familiarity and reuse: the rest of the backend/data stack already
  standardizes on Python (`pyproject.toml`, `docs/environment/` tooling
  decisions from P01.04).

## Options considered

### Runtime language/framework

| Option | Pros | Cons |
|---|---|---|
| **Python (asyncio) service, ONNX Runtime Python bindings** | Matches ONNX Runtime's first-class Python API, reuses the project's existing pinned Python tooling/lint/test stack (P01.04), fastest iteration for contract and fault-injection testing | Higher baseline memory than a compiled runtime; must be watched against the 512 MiB edge limit |
| Go | Lower memory footprint, strong concurrency primitives | ONNX Runtime Go bindings are less mature/maintained than the Python/C++ bindings; would fragment the toolchain from the rest of the Python-based backend for no measured latency requirement that Python cannot meet |
| Rust | Best memory/latency ceiling | Steepest development cost for a project with a fixed academic schedule; ONNX Runtime Rust bindings add an extra dependency-review surface without a demonstrated need at this workload |

### Durable outbox storage

| Option | Pros | Cons |
|---|---|---|
| **SQLite (WAL mode) per edge instance, as named in `docs/REFERENCE_ARCHITECTURE.md`** | Single-file, crash-safe with WAL, no separate server process, trivial to back up/inspect per edge, supports ordered replay with an acknowledgement cursor | Not for high-concurrency multi-writer workloads, which a single edge instance does not need |
| Embedded log-structured queue (e.g., a custom append-only file) | Minimal dependency | Reimplements crash-safety, ordering, and compaction that SQLite's WAL already provides and that P04.07 must prove correct; unjustified engineering risk |
| A second lightweight database server (e.g., Redis persistence) per edge | Familiar API | Adds a stateful process per edge instance inside an already tight resource budget, and blurs the project rule that Redis is never the system of record for durable state |

## Decision

Each edge instance runs as a Python asyncio service using ONNX Runtime's
Python CPU execution provider for inference and SQLite in WAL mode as the
durable outbox, one file per logical edge identity, with an
acknowledgement-tracked replay cursor (P04.07). This keeps one language
across backend and edge code and avoids adding a stateful server process per
edge site.

## Consequences

- `source-code/edge/` (EDGE ownership) contains adapters, validation,
  inference wiring, and the SQLite-backed outbox module; it does not contain
  training/evaluation code, which stays in `source-code/models/`.
- Outbox schema and replay-cursor behavior must be covered by crash/restart
  fault tests (P03.06, P04.07), not assumed correct from SQLite alone.
- Container images for edge instances must stay non-root and within the
  0.75 CPU/512 MiB limit under load testing (P11.01, P11.06).

## Security/privacy/safety effects

The outbox stores only validated, privacy-preserving telemetry/metadata
(ADR-0005, P04.06); it never buffers raw identifiable video. SQLite files
are edge-local and never directly network-exposed; only the MQTT
adapter/gateway path leaves the edge process.

## Revision triggers

- Measured memory use under the async Python runtime cannot meet the 512 MiB
  limit even after profiling/tuning, which would require re-evaluating a
  compiled runtime for the inference/outbox hot path specifically, not the
  whole edge service.

## Related requirements

RQ10, P04.01, P04.05, P04.07-P04.09.
