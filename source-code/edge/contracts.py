"""Locate and compile the versioned JSON Schema contracts the edge enforces."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator


def contracts_root() -> Path:
    override = os.environ.get("EDGE_CONTRACTS_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "contracts"


@lru_cache(maxsize=None)
def load_validator(contract: str, version: str = "v1") -> Draft202012Validator:
    schema_path = contracts_root() / contract / version / "schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)
