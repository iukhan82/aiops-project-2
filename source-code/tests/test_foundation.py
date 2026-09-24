from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(
    not (ROOT / "TASK_REGISTER.md").is_file(),
    reason="markdown project documents are not stored in the repository",
)
def test_required_project_controls_exist() -> None:
    required = {
        "AGENTS.md",
        "Memory.md",
        "PROJECT_PLAN.md",
        "TASK_REGISTER.md",
        "docs/requirements/TRACEABILITY.md",
    }

    assert not [path for path in sorted(required) if not (ROOT / path).is_file()]


def test_confidential_assessment_sources_are_not_in_repository() -> None:
    repository_pdfs = list(ROOT.rglob("*.pdf"))

    assert not [path for path in repository_pdfs if "exam" in path.name.lower()]
