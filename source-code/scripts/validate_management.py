"""Validate the project-management and local-skill baseline using stdlib only."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TASK_PATTERN = re.compile(r"^\| (P\d{2}\.\d{2}) \|", re.MULTILINE)
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)")
REQUIRED_ROOTS = {
    "source-code",
    "diagrams",
    "docs",
    "presentation",
    "skills",
    "workflows",
    "files",
}
VALID_STATUSES = {
    "TODO",
    "IN_PROGRESS",
    "IN_REVIEW",
    "BLOCKED",
    "DONE",
    "DEFERRED",
    "CANCELLED",
}


def task_rows() -> list[list[str]]:
    lines = (ROOT / "TASK_REGISTER.md").read_text(encoding="utf-8").splitlines()
    return [
        [cell.strip() for cell in line.split("|")[1:-1]]
        for line in lines
        if TASK_PATTERN.match(line)
    ]


def validate_tasks(errors: list[str]) -> None:
    rows = task_rows()
    ids = [row[0] for row in rows]
    if len(ids) != len(set(ids)):
        errors.append("task IDs are not unique")
    known = set(ids)
    graph: dict[str, set[str]] = {task_id: set() for task_id in ids}
    for row in rows:
        task_id, dependencies, status = row[0], row[4], row[5]
        if status not in VALID_STATUSES:
            errors.append(f"{task_id}: invalid status {status!r}")
        for dependency in re.findall(r"P\d{2}\.\d{2}", dependencies):
            if dependency not in known:
                errors.append(f"{task_id}: missing dependency {dependency}")
            else:
                graph[task_id].add(dependency)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str, path: list[str]) -> None:
        if task_id in visiting:
            cycle = path[path.index(task_id) :] + [task_id]
            errors.append(f"dependency cycle: {' -> '.join(cycle)}")
            return
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in graph[task_id]:
            visit(dependency, [*path, task_id])
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in ids:
        visit(task_id, [])


def validate_skills(errors: list[str]) -> None:
    skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
    if len(skills) != 16:
        errors.append(f"expected 16 skills, found {len(skills)}")
    for path in skills:
        text = path.read_text(encoding="utf-8")
        if "[TODO" in text or "TODO:" in text:
            errors.append(f"unfinished skill placeholder: {path.relative_to(ROOT)}")
        if not re.match(r"^---\nname: [a-z0-9-]+\ndescription: .+\n---", text):
            errors.append(f"invalid skill frontmatter: {path.relative_to(ROOT)}")
        if not (path.parent / "agents" / "openai.yaml").is_file():
            errors.append(f"missing openai.yaml: {path.parent.relative_to(ROOT)}")


def validate_links(errors: list[str]) -> None:
    for path in ROOT.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for match in LINK_PATTERN.finditer(text):
            target = match.group(1)
            if re.match(r"^(https?://|mailto:|#)", target):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                errors.append(f"broken link in {path.relative_to(ROOT)}: {target}")


def main() -> int:
    errors: list[str] = []
    missing = sorted(name for name in REQUIRED_ROOTS if not (ROOT / name).is_dir())
    if missing:
        errors.append(f"missing required root folders: {', '.join(missing)}")
    validate_tasks(errors)
    validate_skills(errors)
    validate_links(errors)
    if errors:
        print("Management validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Management validation passed: {len(task_rows())} tasks, 16 skills, links OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
