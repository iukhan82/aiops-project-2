# ADR-0005: Edge AI model strategy

- **Status:** Accepted
- **Date:** 2026-09-18
- **Deciders:** ARCH, with EDGE as implementing owner

## Context

The assessment requires real optimized local inference at the edge (Phase
04), not a cloud-only model. The measured development workstation has no
discrete GPU (`docs/environment/WORKSTATION_INVENTORY.md`), and the assessment
target budget likewise does not guarantee an accelerator
(`docs/environment/RESOURCE_BUDGET.md`: "final target set after benchmark").
`docs/PROJECT_CONTEXT.md` requires baseline rules before complex ML and
requires models to abstain or roll back safely.

## Decision drivers

- CPU-only inference must be real and measured, not assumed (P04.09).
- P95 warm inference target below 100 ms on CPU per edge instance
  (`docs/environment/RESOURCE_BUDGET.md`).
- Transparent, explainable baseline required before any learned model
  (`docs/PROJECT_CONTEXT.md`, P04.02).
- Model portability across the edge runtime container without vendor
  lock-in, and safe atomic activation/rollback (P04.08).
- No field-accuracy claim is supportable from synthetic data alone
  (`docs/PROJECT_CONTEXT.md`); the strategy must make that limitation
  structural, not just documented.

## Options considered

### Modeling approach

| Option | Pros | Cons |
|---|---|---|
| **Rule/statistical baseline first, then a small trained model compared against it** | Explainable, fast to implement and verify, gives a correctness floor and a real comparison basis (P04.02, P04.03) | Baseline alone will underperform a trained model on nonlinear patterns, which is expected and documented, not hidden |
| Deep learning model from the start, no baseline | Potentially higher accuracy sooner | No transparent fallback if the model degrades or drifts; violates the project's explicit baseline-before-ML invariant; harder to explain live in P14 walkthroughs |
| Pretrained large vision/traffic foundation model | Minimal training effort | Far exceeds CPU-only latency/resource budget; provenance and license review burden; mismatched to lane/intersection-level tabular and trajectory features this project actually needs |

### Edge inference runtime format

| Option | Pros | Cons |
|---|---|---|
| **ONNX export + ONNX Runtime (CPU execution provider)** | Framework-neutral artifact with an integrity-hashable file (P04.04), broad CPU optimization (graph fusion, quantization), works identically across dev/WSL2/K3s containers without a GPU dependency | Export step must be verified for train/serve parity every release |
| Native framework inference (e.g., serve the PyTorch/TensorFlow model directly) | No export/parity step | Larger runtime dependency footprint in the edge container, weaker integrity/versioning story than a single hashed ONNX artifact, harder atomic activation/rollback |
| TensorRT | Best possible latency | Requires an NVIDIA GPU, unavailable on the measured workstation and unconfirmed on the assessment target; not viable |

## Decision

Build a transparent rule/statistical baseline first (P04.02) and only adopt
a trained model where it measurably beats that baseline on held-out data
(P04.03). Train in any suitable framework, then export to ONNX and serve
exclusively through ONNX Runtime's CPU execution provider inside the edge
runtime container (P04.04-P04.05), with the ONNX file's integrity hash
recorded as part of atomic activation (P04.08). The runtime must support
abstention and rollback to the baseline when the model artifact is invalid,
degraded, or absent.

## Consequences

- Every edge model change ships as an ONNX artifact plus a model card
  recording provenance, training/validation split, comparison against the
  baseline, and measured CPU latency (P04.03, P04.09).
- No GPU-only code path may be required for correctness; GPU acceleration, if
  ever available on a future target, is an optimization, not a dependency.
- `source-code/models/` (EDGE/OPS ownership) holds training, evaluation, and
  registry metadata; `source-code/edge/` holds only the serving path.

## Security/privacy/safety effects

Model activation is atomic and reversible (P04.08); an invalid or unverified
artifact must be refused rather than served. Model inputs stay within the
privacy-preserving metadata path (P04.06); no raw identifiable video is a
model input or artifact.

## Revision triggers

- Measured P95 CPU latency cannot meet the 100 ms warm-inference target after
  quantization/tuning, which would require a smaller model architecture, not
  a change of runtime.
- A confirmed GPU-equipped assessment target becomes available, which could
  add a GPU execution provider as an optional path without removing the
  CPU-only guarantee.

## Related requirements

RQ10, P04.01-P04.09.
