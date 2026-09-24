"""P05.06: network-state pure-logic unit tests (quality/truth-label
aggregation rules). Real-database behavior (windowed aggregation,
carry-forward, freshness, contract-schema conformance) needs the real
P05.01/P05.04 Postgres and is proven by
source-code/backend/state/verify_network_state.py, not here - see
docs/evidence/p05_06_network_state.json.
"""

from backend.state.network_state import _dominant_truth_label, _worst_quality


def test_worst_quality_prefers_invalid_over_suspect_and_valid() -> None:
    assert _worst_quality(["valid", "suspect", "invalid"]) == "invalid"


def test_worst_quality_prefers_suspect_over_valid() -> None:
    assert _worst_quality(["valid", "valid", "suspect"]) == "suspect"


def test_worst_quality_all_valid_stays_valid() -> None:
    assert _worst_quality(["valid", "valid"]) == "valid"


def test_dominant_truth_label_single_label_passes_through() -> None:
    assert _dominant_truth_label({"simulated"}) == "simulated"


def test_dominant_truth_label_mixed_becomes_inferred() -> None:
    assert _dominant_truth_label({"simulated", "operator_entered"}) == "inferred"
