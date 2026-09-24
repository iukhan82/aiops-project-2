"""P04.08: atomic model activation and rollback.

`models/registry/<model_id>/<version>/` packages (P04.04) are staged
independently of which one is *active*. Activation is a separate,
crash-safe step:

- `activate(version)` fully re-verifies that version with
  `model_runtime.load_package` (integrity, schema, golden vectors) *before*
  touching anything durable. A refused version never becomes active and the
  previously active pointer is untouched - "refused... without unsafe
  serving" is enforced by ordering (verify, then commit), not by a
  best-effort check.
- The active pointer is one file (`ACTIVE_VERSION`) written via a temp file
  + `os.replace`, which is atomic on both POSIX and Windows (`MoveFileEx`
  with `MOVEFILE_REPLACE_EXISTING`): a crash mid-write leaves either the old
  content or the new content, never a torn value, and never a value naming
  a version that was not itself just verified.
- `rollback()` re-verifies its target too (the log records history, it is
  not trusted blindly); if the previous version has since been corrupted on
  disk, rollback refuses and the current (still-verified) active version
  keeps serving.

This module only decides *which verified package is active on disk* and
records that decision; hot-swapping a live process's in-memory model is
`edge.runtime.EdgeRuntime.swap_model` (P04.05), called only with the
`OnnxModel` this module already verified - the two mechanisms compose, they
do not duplicate each other.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from edge.model_runtime import ModelError, OnnxModel, load_package

ACTIVE_POINTER_FILENAME = "ACTIVE_VERSION"
ACTIVATION_LOG_FILENAME = "activation_log.jsonl"


class ActivationError(ModelError):
    """No version to activate/roll back to, distinct from a verification failure."""

    code = "activation_error"


@dataclass(frozen=True)
class ActivationRecord:
    activated_at: float
    previous_version: str | None
    new_version: str
    action: str  # "activate" | "rollback"
    reason: str
    model_label: str


class ModelActivator:
    """One instance per model_id (`models/registry/<model_id>/`)."""

    def __init__(self, model_root: Path, feature_version: str) -> None:
        self.model_root = Path(model_root)
        self.feature_version = feature_version
        self._pointer_path = self.model_root / ACTIVE_POINTER_FILENAME
        self._log_path = self.model_root / ACTIVATION_LOG_FILENAME

    # -- read-only state ----------------------------------------------------------------

    def active_version(self) -> str | None:
        try:
            return self._pointer_path.read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None

    def list_versions(self) -> list[str]:
        if not self.model_root.is_dir():
            return []
        return sorted(
            p.name
            for p in self.model_root.iterdir()
            if p.is_dir() and (p / "artifact_manifest.json").is_file()
        )

    def history(self) -> list[ActivationRecord]:
        if not self._log_path.is_file():
            return []
        records = []
        for line in self._log_path.read_text(encoding="utf-8").splitlines():
            if line:
                records.append(ActivationRecord(**json.loads(line)))
        return records

    def package_dir(self, version: str) -> Path:
        return self.model_root / version

    # -- verify-then-commit ---------------------------------------------------------------

    def _verify(self, version: str) -> OnnxModel:
        """Raises ModelError (never commits anything) if `version` cannot be trusted."""
        return load_package(
            self.package_dir(version), expected_feature_version=self.feature_version
        )

    def _commit(self, new_version: str, action: str, reason: str, model: OnnxModel) -> None:
        previous = self.active_version()
        tmp = self._pointer_path.with_name(f".{ACTIVE_POINTER_FILENAME}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(new_version, encoding="utf-8")
        os.replace(tmp, self._pointer_path)  # atomic: never a torn pointer value
        record = ActivationRecord(
            time.time(), previous, new_version, action, reason, model.identity.label
        )
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.__dict__, sort_keys=True) + "\n")

    def activate(self, version: str, reason: str = "manual") -> OnnxModel:
        """Verify `version` fully, and only on success make it the active
        one. On failure, raises and changes nothing durable."""
        model = self._verify(version)  # raises ModelError; nothing committed yet
        self._commit(version, "activate", reason, model)
        return model

    def rollback(self, reason: str = "rollback") -> OnnxModel:
        """Re-verify and activate the version active immediately before the
        current one. Refuses (and leaves the current pointer alone) if
        there is no prior version recorded, or if that prior version can no
        longer be verified - a rollback must never serve something unsafe
        either."""
        history = self.history()
        current = self.active_version()
        target = None
        for record in reversed(history):
            if record.new_version == current and record.previous_version:
                target = record.previous_version
                break
        if target is None:
            raise ActivationError(f"no prior version to roll back to from {current!r}")
        model = self._verify(target)  # a corrupted prior version refuses here, unchanged pointer
        self._commit(target, "rollback", reason, model)
        return model
