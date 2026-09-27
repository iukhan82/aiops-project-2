#!/usr/bin/env python3
"""P13.03: the repository's front doors (the two READMEs and the data cards) say what P13.01-P13.02 and the acceptance sweep actually found, and
point a reader at the real evidence rather than restating it from memory.

    python source-code/scripts/verify_docs.py

Writes `docs/evidence/p13_03_docs.json`. Complements `verify_api_docs.py` (the generated API reference) - this script covers what nothing else
generates: the two READMEs and the data cards.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402

ROOT_README = REPO_ROOT / "README.md"
SOURCE_README = SOURCE_ROOT / "README.md"
DATA_CARDS = REPO_ROOT / "docs" / "DATA_CARDS.md"

ROOT_README_MUST_LINK = (
    "docs/evidence/EVIDENCE_INDEX.md",
    "docs/evidence/DEFECT_REGISTER.md",
    "docs/requirements/ACCEPTANCE_TARGETS.md",
    "docs/requirements/TRACEABILITY.md",
    "docs/api/API_REFERENCE.md",
    "docs/DATA_CARDS.md",
    "docs/runbooks/",
    "source-code/README.md",
)
ROOT_README_MUST_SAY = ("NOT PASSED", "D-01", "assessor")
SOURCE_README_MUST_MENTION = (
    "verify_diagrams.py",
    "verify_api_docs.py",
    "verify_runbooks.py",
    "run_catalog.py",
    "pytest",
)


def main() -> int:
    ev = Evidence("P13.03", "p13_03_docs", docs_name="p13_03_docs")

    root = ROOT_README.read_text(encoding="utf-8")
    missing_links = [link for link in ROOT_README_MUST_LINK if link not in root]
    ev.check(
        "the_root_readme_links_every_key_evidence_and_reference_document",
        not missing_links,
        str(missing_links),
    )
    missing_honesty = [s for s in ROOT_README_MUST_SAY if s not in root]
    ev.check(
        "the_root_readme_states_the_gate_result_and_the_biggest_open_gap_honestly",
        not missing_honesty,
        str(missing_honesty),
    )

    source_readme = SOURCE_README.read_text(encoding="utf-8")
    missing_mentions = [s for s in SOURCE_README_MUST_MENTION if s not in source_readme]
    ev.check(
        "the_source_readme_names_the_real_check_and_run_commands",
        not missing_mentions,
        str(missing_mentions),
    )
    real_folders = {
        p.name for p in SOURCE_ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")
    }
    named_folders = {
        line.split("`")[1].rstrip("/")
        for line in source_readme.splitlines()
        if line.startswith("| `") and line.count("`") >= 2
    }
    undocumented = sorted(real_folders - named_folders - {"__pycache__"})
    ev.check(
        "the_source_readme_layout_table_names_every_top_level_folder",
        not undocumented,
        f"not in the layout table: {undocumented}",
    )

    cards_text = DATA_CARDS.read_text(encoding="utf-8")
    model_cards = sorted(
        (REPO_ROOT / "source-code" / "models" / "registry").glob("*/*/model_card.json")
    )
    ev.check("at_least_one_model_card_exists", bool(model_cards), str(len(model_cards)))
    for card in model_cards:
        doc = json.loads(card.read_text(encoding="utf-8"))
        ev.check(
            f"model_card_{card.parent.parent.name}_names_its_limitations",
            bool(doc.get("limitations")),
            str(card.relative_to(REPO_ROOT)),
        )
    missing_data_card_topics = [
        s
        for s in ("SUMO", "edge_camera", "operations dataset", "emergency call")
        if s.lower() not in cards_text.lower()
    ]
    ev.check(
        "the_data_cards_document_covers_the_simulator_vision_operations_and_emergency_datasets",
        not missing_data_card_topics,
        str(missing_data_card_topics),
    )

    ev.metrics = {"model_cards": len(model_cards)}
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
