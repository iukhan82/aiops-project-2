"""P04.04: export the selected model to ONNX, prove it is the same function
as the trained estimator, and assemble the hash-pinned model package the edge
runtime loads (edge/model_runtime.py).

    python source-code/models/export/export_onnx.py

Verification performed (all recorded in the model card, none assumed):
- onnx.checker passes;
- ONNX Runtime (CPU) probabilities match the scikit-learn estimator on every
  row of every split within 1e-5, and alarm/abstain/clear decisions are
  identical except for rows within 1e-6 of a threshold;
- the loaded package survives edge.model_runtime.load_package (integrity,
  schema, golden vectors).
Reading the test split for parity is a non-selecting use and is logged in the
test-split ledger.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import onnx
import onnxruntime as ort
import skl2onnx
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))

from edge.model_runtime import PACKAGE_FORMAT, load_package, make_session_options  # noqa: E402
from models.dataset.loader import build_feature_table, load_manifest, verify_manifest  # noqa: E402
from models.evaluation.test_gate import record_opening  # noqa: E402

MODEL_ID = "traffic-safety-blockage"
MODEL_VERSION = "1.0.0"
PACKAGE_DIR = SOURCE / "models" / "registry" / MODEL_ID / MODEL_VERSION
LOCAL_MODEL = SOURCE / "models" / "train" / "output" / "model.joblib"
TARGET_OPSET = 17
PROB_TOLERANCE = 1e-5
THRESHOLD_EPS = 1e-6


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, doc: dict) -> None:
    path.write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def _final_estimator(model):
    return model.steps[-1][1] if hasattr(model, "steps") else model


def main() -> None:
    training = json.loads((PACKAGE_DIR / "training_report.json").read_text(encoding="utf-8"))
    comparison = json.loads((PACKAGE_DIR / "comparison_test.json").read_text(encoding="utf-8"))
    if _sha256(LOCAL_MODEL.read_bytes()) != training["estimator_joblib_sha256_local_only"]:
        raise SystemExit("local estimator does not match the one recorded by training")
    problems = verify_manifest()
    if problems:
        raise SystemExit(f"dataset failed integrity check: {problems}")
    manifest = load_manifest()
    estimator = joblib.load(LOCAL_MODEL)
    names = training["data"]["feature_names"]
    policy = training["decision_policy"]

    # ---- convert -----------------------------------------------------------------
    onx = convert_sklearn(
        estimator,
        initial_types=[("features", FloatTensorType([None, len(names)]))],
        options={type(_final_estimator(estimator)): {"zipmap": False}},
        target_opset={"": TARGET_OPSET, "ai.onnx.ml": 3},
    )
    for key, value in {
        "model_id": MODEL_ID,
        "model_version": MODEL_VERSION,
        "feature_version": training["data"]["feature_version"],
        "feature_names": json.dumps(names),
        "dataset_sha256": manifest["dataset_sha256"],
        "source_framework": "scikit-learn",
    }.items():
        prop = onx.metadata_props.add()
        prop.key, prop.value = key, str(value)
    onx.doc_string = (
        f"{MODEL_ID} {MODEL_VERSION}: road-blockage probability from loop-window features"
    )
    onnx.checker.check_model(onx)
    onnx_bytes = onx.SerializeToString()

    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
    (PACKAGE_DIR / "model.onnx").write_bytes(onnx_bytes)

    # ---- parity: ORT vs scikit-learn on every row of every split -----------------
    table = build_feature_table()
    session = ort.InferenceSession(
        str(PACKAGE_DIR / "model.onnx"), make_session_options(), providers=["CPUExecutionProvider"]
    )
    x = table.X.astype(np.float32)
    p_sklearn = estimator.predict_proba(x)[:, 1]
    p_onnx = np.asarray(session.run(None, {"features": x})[1])[:, 1].astype(np.float64)
    max_diff = float(np.max(np.abs(p_sklearn - p_onnx)))
    if max_diff > PROB_TOLERANCE:
        raise SystemExit(f"ONNX/sklearn probability mismatch: max |diff| = {max_diff:.3g}")

    t_alarm = policy["alarm_if_probability_at_least"]
    t_low = policy["clear_below"]

    def decide(p: np.ndarray) -> np.ndarray:
        return np.where(p >= t_alarm, 1, np.where(p >= t_low, -1, 0))

    d_sklearn, d_onnx = decide(p_sklearn), decide(p_onnx)
    near = (np.abs(p_sklearn - t_alarm) < THRESHOLD_EPS) | (
        np.abs(p_sklearn - t_low) < THRESHOLD_EPS
    )
    mismatched = np.flatnonzero((d_sklearn != d_onnx) & ~near)
    if len(mismatched):
        raise SystemExit(f"{len(mismatched)} decision mismatches away from thresholds")
    per_split = {}
    for split in ("train", "validation", "test"):
        mask = table.mask(split=split)
        per_split[split] = {
            "rows": int(mask.sum()),
            "max_abs_probability_diff": float(np.max(np.abs(p_sklearn[mask] - p_onnx[mask]))),
            "decision_mismatches": int(((d_sklearn != d_onnx) & mask).sum()),
        }
    record_opening(
        "onnx_parity",
        manifest["dataset_sha256"],
        table.feature_version,
        {"model_id": MODEL_ID, "model_version": MODEL_VERSION, "max_abs_diff": max_diff},
    )

    # ---- golden vectors (independent sklearn reference) --------------------------
    rng = np.random.default_rng(20260918)
    bands = {
        "alarm": np.flatnonzero(p_sklearn >= t_alarm),
        "abstain": np.flatnonzero((p_sklearn >= t_low) & (p_sklearn < t_alarm)),
        "clear": np.flatnonzero(p_sklearn < t_low),
    }
    chosen = []
    for band, count in (("alarm", 8), ("abstain", 4), ("clear", 8)):
        pool = bands[band]
        if len(pool):
            chosen.extend(
                int(i) for i in rng.choice(pool, size=min(count, len(pool)), replace=False)
            )
    golden_inputs = x[chosen]
    golden = {
        "note": "expected_probability comes from the scikit-learn estimator (float64), an "
        "independent reference for the exported graph",
        "feature_names": names,
        "inputs": golden_inputs.tolist(),
        "expected_probability": p_sklearn[chosen].tolist(),
        "expected_decision": [
            "alarm" if d == 1 else "abstain" if d == -1 else "clear" for d in d_sklearn[chosen]
        ],
    }
    _write_json(PACKAGE_DIR / "golden_vectors.json", golden)

    # ---- io schema + manifest -----------------------------------------------------
    outputs = [o.name for o in session.get_outputs()]
    schema = {
        "input": {"name": "features", "dtype": "float32", "shape": ["N", len(names)]},
        "feature_names": names,
        "feature_version": training["data"]["feature_version"],
        "output_names": outputs,
        "output_semantics": {
            outputs[0]: "int64 predicted label at 0.5 (do not use for decisions)",
            outputs[1]: "float32 [N,2] class probabilities; column 1 = P(road blockage)",
        },
        "decision_policy": {
            "alarm_if_probability_at_least": t_alarm,
            "abstain_if_probability_in": [t_low, t_alarm],
            "clear_below": t_low,
        },
        "input_contract": "finite float32 only; NaN/inf is refused, never imputed by the runtime",
    }
    _write_json(PACKAGE_DIR / "io_schema.json", schema)

    files = {
        name: _sha256((PACKAGE_DIR / name).read_bytes())
        for name in ("model.onnx", "io_schema.json", "golden_vectors.json")
    }
    _write_json(
        PACKAGE_DIR / "artifact_manifest.json",
        {
            "format": PACKAGE_FORMAT,
            "files": files,
            "identity": {
                "model_id": MODEL_ID,
                "model_version": MODEL_VERSION,
                "feature_version": training["data"]["feature_version"],
                "alarm_threshold": t_alarm,
                "abstain_lower": t_low,
            },
        },
    )

    # ---- model card ---------------------------------------------------------------
    model_test, base_test = comparison["model"], comparison["baseline"]

    def key_metrics(report: dict) -> dict:
        r, e = report["rows"], report["episodes"]
        return {
            "precision": r["precision"],
            "recall": r["recall"],
            "f1": r["f1"],
            "precision_ci95": report["rows_ci95"]["precision"],
            "recall_ci95": report["rows_ci95"]["recall"],
            "f1_ci95": report["rows_ci95"]["f1"],
            "rows": r["n"],
            "positive_rows": r["positives"],
            "incidents": e["incidents"],
            "incidents_detected": e["incidents_detected"],
            "incident_recall": e["incident_recall"],
            "median_detection_delay_s": e["median_detection_delay_s"],
            "false_alarm_episodes": e["false_alarm_episodes"],
            "false_alarm_episode_share": e["false_alarm_episode_share"],
            "false_alarm_episodes_per_device_hour": e["false_alarm_episodes_per_device_hour"],
        }

    card = {
        "format": "edge-model-card/1",
        "model_id": MODEL_ID,
        "model_version": MODEL_VERSION,
        "task": "Detect an active road blockage on a corridor segment from a loop detector's "
        "last four 30 s intervals plus its upstream/downstream neighbor loops.",
        "intended_use": "Edge-local candidate incident detection feeding operator review. "
        "Output is an inferred candidate with probability and abstention, never an "
        "autonomous action; protected actions still need policy, approval and audit.",
        "out_of_scope": [
            "any use as evidence of field accuracy (all data is SUMO-simulated)",
            "non-loop sensors, vision, or other incident types (collision, wrong-way, flooding)",
            "direct actuation of signals or road closures",
        ],
        "inputs": {
            "feature_version": training["data"]["feature_version"],
            "feature_names": names,
            "window": "4 x 30 s intervals, past-only; corridor context from adjacent loops",
        },
        "outputs": {"probability": "P(blockage) in [0,1]", "decision": "alarm | abstain | clear"},
        "decision_policy": schema["decision_policy"],
        "provenance": {
            "dataset_sha256": manifest["dataset_sha256"],
            "split_seeds": manifest["split_seeds"],
            "rows": training["data"]["rows"],
            "positives": training["data"]["positives"],
            "training_seed": training["seed"],
            "environment": training["environment"],
            "reproducible_refit_bitwise_identical": training[
                "reproducible_refit_bitwise_identical"
            ],
            "procedure": training["procedure"],
            "reports": ["training_report.json", "comparison_test.json"],
        },
        "selected_candidate": training["selected"],
        "candidates_tried": training["procedure"]["candidates_tried"],
        "held_out_test_opened_once": {
            "model": key_metrics(model_test),
            "rule_baseline": key_metrics(base_test),
            "paired_delta_model_minus_baseline_cluster_bootstrap": comparison[
                "paired_delta_model_minus_baseline_cluster_bootstrap"
            ],
            "abstention": {
                k: comparison["model_abstention"][k]
                for k in ("abstain_rate", "positive_rate_among_abstained")
            },
            "calibration": {
                "brier": comparison["model_reliability"]["brier"],
                "ece": comparison["model_reliability"]["ece"],
            },
            "acc01_verdict": comparison["acc01_verdict"],
        },
        "export": {
            "onnx_sha256": files["model.onnx"],
            "onnx_bytes": len(onnx_bytes),
            "opset": TARGET_OPSET,
            "converter": f"skl2onnx {skl2onnx.__version__}",
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
            "parity": {
                "max_abs_probability_diff_vs_sklearn": max_diff,
                "tolerance": PROB_TOLERANCE,
                "decision_mismatches_away_from_thresholds": int(len(mismatched)),
                "per_split": per_split,
            },
        },
        "limitations": [
            "Trained and evaluated only on SUMO-simulated loop data; no field-accuracy claim follows.",
            "A single physical mechanism (two-lane blockage 25-70 m past the loop) and one "
            "network geometry; distance and demand generalization is untested outside them.",
            "Labels are ground truth (blockage active), not 'detectable yet': at low flow or long "
            "blockage-to-loop distance the queue never reaches a detector and no loop-based "
            "method can see the incident. See recall by distance/demand in comparison_test.json.",
            "The false-alarm budget (<=10% of alarm episodes) held on validation; on the test "
            "split both detectors ended slightly above it (see held_out_test_opened_once).",
            "Corridor-context features need neighbor loops; at corridor ends they are structurally "
            "absent and the model falls back on own-window evidence only.",
            "Test-split sample is small (48 incidents, 24 runs): confidence intervals are wide.",
        ],
        "privacy": "Inputs are aggregate loop counts/occupancy/speed; no personal or identifying data.",
    }
    _write_json(PACKAGE_DIR / "model_card.json", card)

    # ---- the package must survive the runtime's own verification ------------------
    loaded = load_package(PACKAGE_DIR, expected_feature_version=training["data"]["feature_version"])
    probability, decision, latency = loaded.infer(x[chosen[0]])
    print(
        json.dumps(
            {
                "package": str(PACKAGE_DIR.relative_to(SOURCE)),
                "onnx_bytes": len(onnx_bytes),
                "onnx_sha256": files["model.onnx"],
                "identity": loaded.identity.label,
                "max_abs_probability_diff_vs_sklearn": max_diff,
                "decision_mismatches_away_from_thresholds": int(len(mismatched)),
                "golden_vectors": len(chosen),
                "smoke_inference": {
                    "probability": round(probability, 4),
                    "decision": decision,
                    "latency_ms": round(latency, 3),
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
