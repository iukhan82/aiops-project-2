"""P04.08: atomic model activation and rollback - refusal never commits,
the pointer write is atomic, rollback re-verifies, and activation composes
with the live runtime's own model swap."""

import json
import shutil
from pathlib import Path

import pytest

from edge.activation import ActivationError, ModelActivator
from edge.features import FEATURE_VERSION
from edge.model_runtime import IntegrityError, ModelError
from edge.runtime import EdgeConfig, EdgeRuntime, ListSink, MemorySequence

REAL_PACKAGE = (
    Path(__file__).resolve().parents[1]
    / "models"
    / "registry"
    / "traffic-safety-blockage"
    / "1.0.0"
)
BASELINE = (
    Path(__file__).resolve().parents[1] / "models" / "registry" / "baseline" / "baseline_v1.json"
)


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage_version(model_root: Path, version: str, *, break_it: bool = False) -> Path:
    """A real, working package copied under a new version label - two
    'different' releases of the same model, which is realistic for a
    metadata-only bump and keeps the test independent of training."""
    dest = model_root / version
    shutil.copytree(REAL_PACKAGE, dest)
    manifest = json.loads((dest / "artifact_manifest.json").read_text(encoding="utf-8"))
    manifest["identity"]["model_version"] = version
    (dest / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if break_it:
        data = bytearray((dest / "model.onnx").read_bytes())
        data[5] ^= 0xFF
        (dest / "model.onnx").write_bytes(bytes(data))
    return dest


@pytest.fixture()
def model_root(tmp_path: Path) -> Path:
    root = tmp_path / "traffic-safety-blockage"
    root.mkdir()
    return root


# --- basic activation ----------------------------------------------------------------


def test_no_pointer_until_first_activation(model_root: Path) -> None:
    activator = ModelActivator(model_root, FEATURE_VERSION)
    assert activator.active_version() is None
    assert activator.list_versions() == []


def test_activate_verifies_then_commits_the_pointer(model_root: Path) -> None:
    _stage_version(model_root, "1.0.0")
    activator = ModelActivator(model_root, FEATURE_VERSION)
    model = activator.activate("1.0.0")
    assert activator.active_version() == "1.0.0"
    assert model.identity.model_version == "1.0.0"
    assert (model_root / "ACTIVE_VERSION").read_text(encoding="utf-8") == "1.0.0"


def test_activating_a_second_version_switches_the_pointer(model_root: Path) -> None:
    _stage_version(model_root, "1.0.0")
    _stage_version(model_root, "1.0.1")
    activator = ModelActivator(model_root, FEATURE_VERSION)
    activator.activate("1.0.0")
    activator.activate("1.0.1")
    assert activator.active_version() == "1.0.1"


def test_list_versions_reflects_staged_packages_not_activation_state(model_root: Path) -> None:
    _stage_version(model_root, "1.0.0")
    _stage_version(model_root, "1.1.0")
    activator = ModelActivator(model_root, FEATURE_VERSION)
    assert activator.list_versions() == ["1.0.0", "1.1.0"]
    assert activator.active_version() is None  # staged is not the same as active


# --- refusal never commits anything ---------------------------------------------------


def test_invalid_version_is_refused_and_pointer_is_untouched(model_root: Path) -> None:
    _stage_version(model_root, "1.0.0")
    _stage_version(model_root, "1.0.1", break_it=True)
    activator = ModelActivator(model_root, FEATURE_VERSION)
    activator.activate("1.0.0")
    with pytest.raises(IntegrityError):
        activator.activate("1.0.1")
    assert activator.active_version() == "1.0.0"  # unchanged: no unsafe serving


def test_first_ever_activation_of_a_bad_version_leaves_no_pointer_at_all(model_root: Path) -> None:
    _stage_version(model_root, "1.0.0", break_it=True)
    activator = ModelActivator(model_root, FEATURE_VERSION)
    with pytest.raises(ModelError):
        activator.activate("1.0.0")
    assert activator.active_version() is None
    assert not (model_root / "ACTIVE_VERSION").exists()


def test_activating_a_nonexistent_version_is_refused(model_root: Path) -> None:
    activator = ModelActivator(model_root, FEATURE_VERSION)
    with pytest.raises(ModelError):
        activator.activate("9.9.9")
    assert activator.active_version() is None


def test_feature_version_mismatch_is_refused_at_activation(model_root: Path) -> None:
    _stage_version(model_root, "1.0.0")
    activator = ModelActivator(model_root, "loop-window/999")
    with pytest.raises(ModelError):
        activator.activate("1.0.0")
    assert activator.active_version() is None


# --- atomicity of the pointer write ---------------------------------------------------


def test_pointer_write_is_atomic_via_tmp_file_and_replace(model_root: Path, monkeypatch) -> None:
    _stage_version(model_root, "1.0.0")
    activator = ModelActivator(model_root, FEATURE_VERSION)
    activator.activate("1.0.0")

    _stage_version(model_root, "1.0.1")
    real_replace = __import__("os").replace

    def crash_before_replace(*args, **kwargs):
        raise OSError("simulated crash between temp-file write and rename")

    monkeypatch.setattr("edge.activation.os.replace", crash_before_replace)
    with pytest.raises(OSError):
        activator.activate("1.0.1")
    monkeypatch.setattr("edge.activation.os.replace", real_replace)

    assert activator.active_version() == "1.0.0"  # crash left the old pointer intact
    leftover_tmp = list(model_root.glob(".ACTIVE_VERSION.*.tmp"))
    assert (
        leftover_tmp and leftover_tmp[0].read_text(encoding="utf-8") == "1.0.1"
    )  # partial write never adopted


def test_pointer_content_is_never_a_torn_value(model_root: Path) -> None:
    _stage_version(model_root, "1.0.0")
    _stage_version(model_root, "1.0.1")
    activator = ModelActivator(model_root, FEATURE_VERSION)
    activator.activate("1.0.0")
    activator.activate("1.0.1")
    content = (model_root / "ACTIVE_VERSION").read_text(encoding="utf-8")
    assert content in ("1.0.0", "1.0.1")  # exactly one complete value, never a mix


# --- rollback ---------------------------------------------------------------------------


def test_rollback_restores_the_previous_version_and_reverifies_it(model_root: Path) -> None:
    _stage_version(model_root, "1.0.0")
    _stage_version(model_root, "1.0.1")
    activator = ModelActivator(model_root, FEATURE_VERSION)
    activator.activate("1.0.0")
    activator.activate("1.0.1")
    model = activator.rollback()
    assert activator.active_version() == "1.0.0"
    assert model.identity.model_version == "1.0.0"


def test_rollback_with_no_history_is_refused(model_root: Path) -> None:
    _stage_version(model_root, "1.0.0")
    activator = ModelActivator(model_root, FEATURE_VERSION)
    activator.activate("1.0.0")
    with pytest.raises(ActivationError):
        activator.rollback()
    assert activator.active_version() == "1.0.0"  # unchanged


def test_rollback_to_a_since_corrupted_version_refuses_and_keeps_current_serving(
    model_root: Path,
) -> None:
    _stage_version(model_root, "1.0.0")
    _stage_version(model_root, "1.0.1")
    activator = ModelActivator(model_root, FEATURE_VERSION)
    activator.activate("1.0.0")
    activator.activate("1.0.1")

    # 1.0.0 rots on disk after it was deactivated (disk fault, tampering, ...)
    data = bytearray((model_root / "1.0.0" / "model.onnx").read_bytes())
    data[3] ^= 0xFF
    (model_root / "1.0.0" / "model.onnx").write_bytes(bytes(data))

    with pytest.raises(ModelError):
        activator.rollback()
    assert activator.active_version() == "1.0.1"  # still serving the last known-good version


def test_activation_log_records_full_history_in_order(model_root: Path) -> None:
    _stage_version(model_root, "1.0.0")
    _stage_version(model_root, "1.0.1")
    activator = ModelActivator(model_root, FEATURE_VERSION)
    activator.activate("1.0.0", reason="initial deploy")
    activator.activate("1.0.1", reason="new release")
    activator.rollback(reason="1.0.1 regressed in canary")

    history = activator.history()
    assert [r.action for r in history] == ["activate", "activate", "rollback"]
    assert [r.new_version for r in history] == ["1.0.0", "1.0.1", "1.0.0"]
    assert history[1].previous_version == "1.0.0"
    assert history[2].previous_version == "1.0.1"
    assert history[2].reason == "1.0.1 regressed in canary"
    assert all(r.model_label.startswith("traffic-safety-blockage/") for r in history)


def test_repeated_rollback_walks_back_through_real_history(model_root: Path) -> None:
    _stage_version(model_root, "1.0.0")
    _stage_version(model_root, "1.0.1")
    _stage_version(model_root, "1.0.2")
    activator = ModelActivator(model_root, FEATURE_VERSION)
    activator.activate("1.0.0")
    activator.activate("1.0.1")
    activator.activate("1.0.2")
    assert activator.rollback().identity.model_version == "1.0.1"
    assert activator.active_version() == "1.0.1"


# --- composes with the live runtime's in-memory swap (P04.05) -------------------------


def _registry_file(tmp_path: Path) -> Path:
    from edge_helpers import make_chain_registry

    path = tmp_path / "devices.jsonl"
    path.write_text(
        "\n".join(json.dumps(d) for d in make_chain_registry().all()) + "\n", encoding="utf-8"
    )
    return path


def test_activation_result_can_hot_swap_a_live_runtime_without_a_gap(tmp_path: Path) -> None:
    model_root = tmp_path / "reg" / "traffic-safety-blockage"
    model_root.mkdir(parents=True)
    _stage_version(model_root, "1.0.0")
    _stage_version(model_root, "1.0.1")
    activator = ModelActivator(model_root, FEATURE_VERSION)
    activator.activate("1.0.0")  # activator is the source of truth for what's active

    config = EdgeConfig(
        site_id="corridor-a",
        runtime_device_id="edge-runtime-corridor-a",
        geometry_version="2026-09-18.1",
        registry_path=_registry_file(tmp_path),
        baseline_path=BASELINE,
        model_dir=model_root / activator.active_version(),
    )
    runtime = EdgeRuntime.build(config, ListSink(), MemorySequence())
    assert runtime.health().mode == "model" and not runtime.health().degraded

    new_model = activator.activate("1.0.1")
    runtime.swap_model(new_model)
    assert runtime.health().model.startswith("traffic-safety-blockage/1.0.1@")
    assert runtime.health().mode == "model" and not runtime.health().degraded

    restored = activator.rollback()
    runtime.swap_model(restored)
    assert runtime.health().model.startswith("traffic-safety-blockage/1.0.0@")


def test_a_refused_activation_never_reaches_swap_model_current_model_keeps_serving(
    tmp_path: Path,
) -> None:
    model_root = tmp_path / "reg" / "traffic-safety-blockage"
    model_root.mkdir(parents=True)
    _stage_version(model_root, "1.0.0")
    _stage_version(model_root, "1.0.1", break_it=True)
    activator = ModelActivator(model_root, FEATURE_VERSION)

    config = EdgeConfig(
        site_id="corridor-a",
        runtime_device_id="edge-runtime-corridor-a",
        geometry_version="2026-09-18.1",
        registry_path=_registry_file(tmp_path),
        baseline_path=BASELINE,
        model_dir=model_root / "1.0.0",
    )
    runtime = EdgeRuntime.build(config, ListSink(), MemorySequence())
    serving_before = runtime.health().model

    with pytest.raises(ModelError):
        model = activator.activate("1.0.1")
        runtime.swap_model(model)  # unreachable: activate() already raised

    assert runtime.health().model == serving_before  # never touched
    assert not runtime.health().degraded
