"""P11.01: the service images' dependency closure is a generated subset of the reviewed root lock, and it is current."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_service_lock.py"
spec = importlib.util.spec_from_file_location("build_service_lock", SCRIPT)
lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock)


def test_the_service_lock_is_current_and_has_no_problem():
    text, problems = lock.build()
    assert problems == []
    assert lock.OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n") == text


def test_every_service_pin_is_the_root_locks_pin_and_the_closure_is_smaller():
    root = lock.root_pins()
    pinned = [line for line in lock.OUTPUT.read_text(encoding="utf-8").splitlines() if "==" in line]
    assert 0 < len(pinned) < len(root)
    for line in pinned:
        name, version = line.split("==")
        assert root[lock.canonicalize_name(name)] == version


def test_nothing_that_belongs_to_development_reaches_a_service_image():
    text = lock.OUTPUT.read_text(encoding="utf-8").lower()
    for dev_only in (
        "pytest",
        "ruff",
        "bandit",
        "matplotlib",
        "onnxruntime",
        "kubernetes-validate",
        "paramiko",
    ):
        assert dev_only not in text
