import json
from pathlib import Path

import jsonschema
import pytest

CONTRACTS_ROOT = Path(__file__).resolve().parents[1] / "contracts"


def _discover_contract_dirs() -> list[Path]:
    return sorted(CONTRACTS_ROOT.glob("*/v*"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "contract_dir",
    _discover_contract_dirs(),
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_valid_examples_pass(contract_dir: Path) -> None:
    schema = _load(contract_dir / "schema.json")
    valid_examples = sorted((contract_dir / "examples").glob("valid-*.json"))
    assert valid_examples, f"no valid-*.json examples under {contract_dir}"
    for example_path in valid_examples:
        instance = _load(example_path)
        jsonschema.validate(instance=instance, schema=schema)


@pytest.mark.parametrize(
    "contract_dir",
    _discover_contract_dirs(),
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_invalid_examples_fail(contract_dir: Path) -> None:
    schema = _load(contract_dir / "schema.json")
    invalid_examples = sorted((contract_dir / "examples").glob("invalid-*.json"))
    assert invalid_examples, f"no invalid-*.json examples under {contract_dir}"
    for example_path in invalid_examples:
        instance = _load(example_path)
        reason = instance.pop("_invalid_because", None)
        assert reason, f"{example_path} is missing an _invalid_because explanation"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=instance, schema=schema)


@pytest.mark.parametrize(
    "contract_dir",
    _discover_contract_dirs(),
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_schema_version_is_semantic(contract_dir: Path) -> None:
    schema = _load(contract_dir / "schema.json")
    version = schema["properties"]["schema_version"]["const"]
    parts = version.split(".")
    assert len(parts) == 3 and all(part.isdigit() for part in parts), (
        f"{contract_dir}: schema_version {version!r} is not major.minor.patch"
    )
    major = parts[0]
    assert contract_dir.name == f"v{major}", (
        f"{contract_dir}: directory version must match schema_version major ({major})"
    )
