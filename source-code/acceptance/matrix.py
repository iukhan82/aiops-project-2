"""The evidence matrix engine (P12.01, P12.02, P12.04, P12.07): a row is a claim about the platform, and it stands only on named checks of named evidence files.

A `Row` says what it claims (`covered`, `partial` with what is not shown, or `gap`), and cites `Cite`s: an evidence file under `docs/evidence/` and a regular expression that must match
the name of at least `min` checks of it (a check of a verifier, or the title of a browser test), every one of them passing. The engine reports, per row, what the cites found. A cited check
that is missing, or that fails, is a FAILURE of the matrix - the evidence contradicts the claim or has moved - whatever the row says. A `partial` or `gap` row must name what is not
shown and who owns closing it; a `covered` row with no cite is refused. The matrix cannot say more than the evidence does, and it cannot hide what the evidence does not say.

Nothing here runs a verifier: `run_catalog.py` and the labs do, and write the evidence this reads.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = REPO_ROOT / "docs" / "evidence"


@dataclass(frozen=True)
class Cite:
    file: str  # docs/evidence name, without .json
    pattern: str  # regular expression, searched (case-insensitive) in the names of the file's checks or browser-test titles
    min: int = 1  # at least this many matches; all of them must pass


@dataclass(frozen=True)
class Row:
    id: str
    title: str
    cites: tuple[Cite, ...] = ()
    status: str = "covered"  # covered | partial | gap: what the row claims when its cites hold
    limit: str = ""  # what is not shown (partial and gap rows)
    owner: str = ""  # who closes it: a role code and a task (partial and gap rows)
    links: tuple[str, ...] = ()  # requirement ids (RQ..) and acceptance targets (LAT-.., SAFE-..)
    detail: str = ""  # what the cited checks show, in a sentence


@dataclass
class Result:
    row: Row
    verdict: str  # covered | partial | gap | FAILED
    found: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


SOURCE_ROOT = REPO_ROOT / "source-code"


@lru_cache(maxsize=None)
def load(name: str) -> dict | None:
    if name.startswith("pytest:"):
        # a unit-test file: each `test_...` function is an entry. That they pass is the `unit-tests` run's business, which the matrix verifiers require to have passed.
        path = SOURCE_ROOT / name.removeprefix("pytest:")
        if not path.exists():
            return None
        tests = re.findall(
            r"^(?:async )?def (test_\w+)", path.read_text(encoding="utf-8"), re.MULTILINE
        )
        return {
            "name": name,
            "entries": dict.fromkeys(tests, True),
            "generated_at": "",
            "all_passed": None,
            "task": None,
        }
    if name.startswith("doc:"):
        # a file that must exist (a document, a diagram, an export): its path is the entry
        rel = name.removeprefix("doc:")
        return (
            {
                "name": name,
                "entries": {rel: True},
                "generated_at": "",
                "all_passed": None,
                "task": None,
            }
            if (REPO_ROOT / rel).exists()
            else None
        )
    path = EVIDENCE / f"{name}.json"
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries: dict[str, bool] = {}
    if isinstance(doc.get("checks"), dict):
        entries = {k: bool(v) for k, v in doc["checks"].items()}
    elif isinstance(doc.get("tests"), list):
        entries = {t["title"]: t["status"] == "passed" for t in doc["tests"]}
    return {
        "name": name,
        "entries": entries,
        "generated_at": doc.get("generated_at", ""),
        "all_passed": doc.get("all_passed"),
        "task": doc.get("task"),
    }


def evaluate(row: Row) -> Result:
    result = Result(row, row.status)
    if row.status in ("partial", "gap") and not (row.limit and row.owner):
        result.problems.append("a partial or gap row must say what is not shown and who owns it")
    if row.status == "covered" and not row.cites:
        result.problems.append("a covered row must cite evidence")
    for cite in row.cites:
        doc = load(cite.file)
        if doc is None:
            result.problems.append(f"{cite.file}: no such evidence file")
            continue
        matched = {
            k: v for k, v in doc["entries"].items() if re.search(cite.pattern, k, re.IGNORECASE)
        }
        failing = sorted(k for k, v in matched.items() if not v)
        result.found.append(
            {
                "file": cite.file,
                "pattern": cite.pattern,
                "matched": len(matched),
                "failing": failing,
                "generated_at": doc["generated_at"],
            }
        )
        if len(matched) < cite.min:
            result.problems.append(
                f"{cite.file}: /{cite.pattern}/ matched {len(matched)} check(s), needed {cite.min}"
            )
        if failing:
            result.problems.append(f"{cite.file}: failing {failing[:3]}")
    if result.problems:
        result.verdict = "FAILED"
    return result


def evaluate_all(rows: tuple[Row, ...] | list[Row]) -> list[Result]:
    return [evaluate(r) for r in rows]


def tally(results: list[Result]) -> dict[str, int]:
    out = {"covered": 0, "partial": 0, "gap": 0, "FAILED": 0}
    for r in results:
        out[r.verdict] = out.get(r.verdict, 0) + 1
    return out


def freshest_and_oldest(results: list[Result]) -> tuple[str, str]:
    stamps = [f["generated_at"] for r in results for f in r.found if f["generated_at"]]
    return (max(stamps), min(stamps)) if stamps else ("", "")


def render(title: str, results: list[Result], intro: str = "") -> str:
    lines = [f"## {title}", ""]
    if intro:
        lines += [intro, ""]
    counts = tally(results)
    lines += [
        f"**{counts['covered']} covered, {counts['partial']} partial, {counts['gap']} gap, {counts['FAILED']} failed.**",
        "",
        "| Row | What | Verdict | Evidence | Not shown / owner |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        cited = "<br>".join(f"`{f['file']}` ({f['matched']})" for f in r.found) or "-"
        rest = (f"{r.row.limit} **Owner:** {r.row.owner}" if r.row.limit else "") or (
            "; ".join(r.problems) if r.problems else ""
        )
        lines.append(
            f"| {r.row.id} | {r.row.title.replace('|', '/')} | {r.verdict} | {cited} | {rest.replace('|', '/')} |"
        )
    lines.append("")
    return "\n".join(lines)
