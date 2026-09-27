"""P10.08: the edge runtime serves the version the activation pointer names, so a rollback takes effect on restart."""

import json
from pathlib import Path

from edge.main import load_config


def _config(tmp_path: Path, **extra) -> Path:
    doc = {
        "site_id": "s",
        "runtime_device_id": "d",
        "geometry_version": "g",
        "registry_path": "devices.jsonl",
        "baseline_path": "baseline.json",
        **extra,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_model_root_resolves_to_the_active_version(tmp_path):
    root = tmp_path / "registry" / "m"
    root.mkdir(parents=True)
    (root / "ACTIVE_VERSION").write_text("1.0.1\n", encoding="utf-8")
    config = load_config(_config(tmp_path, model_root=str(root)))
    assert config.model_dir == root / "1.0.1"


def test_a_missing_pointer_becomes_a_model_error_not_a_silent_default(tmp_path):
    root = tmp_path / "registry" / "m"
    root.mkdir(parents=True)
    config = load_config(_config(tmp_path, model_root=str(root)))
    assert (
        config.model_dir == root / "NO_ACTIVE_VERSION"
    )  # load_package refuses it and the runtime counts the error


def test_an_explicit_model_dir_still_works_and_no_model_stays_none(tmp_path):
    assert load_config(_config(tmp_path, model_dir="x/1.0.0")).model_dir == Path("x/1.0.0")
    assert load_config(_config(tmp_path)).model_dir is None
