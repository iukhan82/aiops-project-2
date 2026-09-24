"""P04.04: verified ONNX model loading and CPU inference for the edge.

A model package is a directory:

    model.onnx              the network (framework-neutral, hash-pinned)
    io_schema.json          input/output names, dtypes, shapes, feature order
    golden_vectors.json     fixed inputs with expected probabilities
    artifact_manifest.json  sha256 of every file above + model identity
    model_card.json         provenance, evaluation, limitations (read-only here)

`load_package` refuses to serve anything it cannot prove is the artifact
that was released: missing files, hash mismatch, schema drift, an ONNX that
will not load, or a golden-vector disagreement (a degraded/corrupted graph
that still loads) all raise a typed `ModelError`. The runtime turns any such
error into a fallback to the rule baseline (P04.05/P04.08); it never serves
an unverified model.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort

PACKAGE_FORMAT = "edge-model-package/1"
GOLDEN_TOLERANCE = 1e-5
PACKAGE_FILES = ("model.onnx", "io_schema.json", "golden_vectors.json")


class ModelError(Exception):
    """Base class: the model package cannot be trusted or served."""

    code = "model_error"


class ArtifactMissing(ModelError):
    code = "artifact_missing"


class IntegrityError(ModelError):
    code = "integrity_mismatch"


class SchemaMismatch(ModelError):
    code = "schema_mismatch"


class LoadError(ModelError):
    code = "load_failed"


class GoldenMismatch(ModelError):
    code = "golden_mismatch"


class InferenceError(ModelError):
    code = "inference_failed"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class DecisionPolicy:
    alarm_threshold: float
    abstain_lower: float

    def decide(self, probability: float) -> str:
        if probability >= self.alarm_threshold:
            return "alarm"
        if probability >= self.abstain_lower:
            return "abstain"
        return "clear"


@dataclass(frozen=True)
class ModelIdentity:
    model_id: str
    model_version: str
    feature_version: str
    feature_names: tuple[str, ...]
    onnx_sha256: str
    policy: DecisionPolicy

    @property
    def label(self) -> str:
        return f"{self.model_id}/{self.model_version}@sha256:{self.onnx_sha256[:12]}"


class OnnxModel:
    def __init__(self, identity: ModelIdentity, session: ort.InferenceSession) -> None:
        self.identity = identity
        self._session = session
        self._input = session.get_inputs()[0].name
        self._n_features = len(identity.feature_names)

    def predict_proba(self, values: np.ndarray) -> np.ndarray:
        """Positive-class probability for each row; refuses malformed input."""
        x = np.asarray(values, dtype=np.float32)
        if x.ndim == 1:
            x = x.reshape(1, -1)
        if x.ndim != 2 or x.shape[1] != self._n_features:
            raise InferenceError(f"expected shape [N, {self._n_features}], got {list(x.shape)}")
        if not np.isfinite(x).all():
            raise InferenceError("non-finite feature value (NaN/inf); refusing to infer")
        try:
            outputs = self._session.run(None, {self._input: x})
        except Exception as exc:  # onnxruntime raises many concrete types
            raise InferenceError(f"onnxruntime failed: {exc}") from exc
        probabilities = np.asarray(outputs[1])
        if probabilities.shape != (x.shape[0], 2) or not np.isfinite(probabilities).all():
            raise InferenceError("model produced malformed or non-finite probabilities")
        return probabilities[:, 1].astype(np.float64)

    def infer(self, values: np.ndarray) -> tuple[float, str, float]:
        """(probability, decision, latency_ms) for one feature vector."""
        start = time.perf_counter()
        probability = float(self.predict_proba(values)[0])
        latency_ms = (time.perf_counter() - start) * 1000.0
        return probability, self.identity.policy.decide(probability), latency_ms


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArtifactMissing(f"missing {path.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaMismatch(f"unreadable {path.name}: {exc}") from exc


def make_session_options() -> ort.SessionOptions:
    """Bounded CPU use: 1 thread, no spinning, sequential execution."""
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.log_severity_level = 3
    ort.disable_telemetry_events()
    return options


def load_package(package_dir: Path, expected_feature_version: str | None = None) -> OnnxModel:
    package_dir = Path(package_dir)
    manifest = _read_json(package_dir / "artifact_manifest.json")
    if manifest.get("format") != PACKAGE_FORMAT:
        raise SchemaMismatch(f"unsupported package format {manifest.get('format')!r}")

    for name in PACKAGE_FILES:
        path = package_dir / name
        if not path.is_file():
            raise ArtifactMissing(f"missing {name}")
        expected = manifest.get("files", {}).get(name)
        if not expected:
            raise SchemaMismatch(f"manifest has no hash for {name}")
        if sha256_file(path) != expected:
            raise IntegrityError(f"{name} does not match its released sha256")

    schema = _read_json(package_dir / "io_schema.json")
    identity_doc = manifest.get("identity", {})
    try:
        feature_names = tuple(schema["feature_names"])
        identity = ModelIdentity(
            model_id=identity_doc["model_id"],
            model_version=identity_doc["model_version"],
            feature_version=identity_doc["feature_version"],
            feature_names=feature_names,
            onnx_sha256=manifest["files"]["model.onnx"],
            policy=DecisionPolicy(
                alarm_threshold=float(identity_doc["alarm_threshold"]),
                abstain_lower=float(identity_doc["abstain_lower"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaMismatch(f"malformed package metadata: {exc}") from exc
    if expected_feature_version and identity.feature_version != expected_feature_version:
        raise SchemaMismatch(
            f"model expects features {identity.feature_version!r}, "
            f"runtime provides {expected_feature_version!r}"
        )
    if not 0.0 <= identity.policy.abstain_lower <= identity.policy.alarm_threshold <= 1.0:
        raise SchemaMismatch("decision policy thresholds are inconsistent")

    try:
        session = ort.InferenceSession(
            str(package_dir / "model.onnx"),
            sess_options=make_session_options(),
            providers=["CPUExecutionProvider"],
        )
    except Exception as exc:
        raise LoadError(f"onnxruntime cannot load model.onnx: {exc}") from exc

    declared = schema.get("input", {})
    actual = session.get_inputs()
    if (
        len(actual) != 1
        or actual[0].name != declared.get("name")
        or actual[0].type != "tensor(float)"
        or list(actual[0].shape)[1:] != [len(feature_names)]
    ):
        raise SchemaMismatch("ONNX input does not match io_schema.json")
    if [o.name for o in session.get_outputs()][:2] != schema.get("output_names", [])[:2]:
        raise SchemaMismatch("ONNX outputs do not match io_schema.json")

    model = OnnxModel(identity, session)
    _check_golden(model, _read_json(package_dir / "golden_vectors.json"))
    return model


def _check_golden(model: OnnxModel, golden: dict) -> None:
    try:
        inputs = np.asarray(golden["inputs"], dtype=np.float32)
        expected = np.asarray(golden["expected_probability"], dtype=np.float64)
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaMismatch(f"malformed golden vectors: {exc}") from exc
    try:
        actual = model.predict_proba(inputs)
    except InferenceError as exc:
        raise GoldenMismatch(f"golden vectors could not be evaluated: {exc}") from exc
    worst = float(np.max(np.abs(actual - expected)))
    if worst > GOLDEN_TOLERANCE:
        raise GoldenMismatch(
            f"golden vectors disagree with the released model (max |diff|={worst:.3g})"
        )
