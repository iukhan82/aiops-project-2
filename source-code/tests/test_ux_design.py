"""P08.03: the design handoff's checkable parts (no database, no browser).
Evidence with the measured ratios and the full status coverage:
docs/evidence/p08_03_design.json."""

import importlib.util
import json
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
_spec = importlib.util.spec_from_file_location("ux_design", FRONTEND / "tools" / "design.py")
design = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(design)

TOKENS = json.loads(design.TOKENS.read_text(encoding="utf-8"))
STATUS = json.loads(design.STATUS.read_text(encoding="utf-8"))


def test_contrast_matches_the_wcag_reference_values() -> None:
    assert round(design.contrast("#000000", "#FFFFFF"), 2) == 21.0
    assert (
        round(design.contrast("#777777", "#FFFFFF"), 2) == 4.48
    )  # the well-known just-failing grey
    assert design.contrast("#FFFFFF", "#FFFFFF") == 1.0


def test_every_declared_pair_meets_its_minimum() -> None:
    for p in TOKENS["contrast_pairs"]:
        assert design.contrast(TOKENS["color"][p["fg"]], TOKENS["color"][p["bg"]]) >= p["min"], p


def test_tokens_css_is_current() -> None:
    assert design.CSS.read_text(encoding="utf-8") == design.render_css(TOKENS)


def test_reduced_motion_collapses_every_duration() -> None:
    css = design.render_css(TOKENS)
    assert "prefers-reduced-motion: reduce" in css
    for name in TOKENS["motion"]:
        if name.startswith("duration"):
            assert f"--motion-{name}: 0.01ms;" in css


def test_no_two_states_of_a_domain_share_a_shape_or_a_label() -> None:
    for domain, states in STATUS["domains"].items():
        shapes = [s["shape"] for s in states.values()]
        labels = [s["label"] for s in states.values()]
        assert len(set(shapes)) == len(shapes), domain
        assert len(set(labels)) == len(labels), domain


def test_a_command_can_never_look_approved_while_the_policy_was_unavailable() -> None:
    cmd = STATUS["domains"]["command"]
    assert cmd["requested_policy_unavailable"]["tone"] != cmd["approved"]["tone"]
    assert cmd["requested_policy_unavailable"]["shape"] != cmd["approved"]["shape"]


def test_an_unknown_outcome_is_never_styled_as_success() -> None:
    out = STATUS["domains"]["outcome"]
    assert out["unknown"]["tone"] != "ok" and out["unsafe"]["tone"] == "danger"


def test_every_status_uses_a_defined_shape_and_tone() -> None:
    for states in STATUS["domains"].values():
        for s in states.values():
            assert (
                s["shape"] in STATUS["shapes"]
                and s["tone"] in STATUS["tones"]
                and s["label"]
                and s["meaning"]
            )


def test_backend_enumerations_are_all_covered() -> None:
    for domain, values in design.backend_enumerations().items():
        assert values <= set(STATUS["domains"][domain]), domain
