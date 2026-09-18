from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "check.yml"


def load_workflow() -> dict[str, object]:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_workflow_has_least_privilege_and_required_jobs() -> None:
    workflow = load_workflow()

    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"python", "frontend", "repository-security"}
    assert all("permissions" not in job for job in workflow["jobs"].values())


def test_workflow_runs_exact_install_and_security_gates() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    required = {
        "source-code/requirements-lock.txt",
        "python source-code/scripts/check.py",
        "npm ci --ignore-scripts",
        "npm run check",
        "--scanners vuln,misconfig,secret",
        "--severity CRITICAL,HIGH",
        "--skip-dirs /workspace/.venv",
        "--skip-dirs /workspace/source-code/frontend/node_modules",
        "persist-credentials: false",
    }
    assert not [item for item in sorted(required) if item not in text]
