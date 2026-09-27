#!/usr/bin/env python3
"""P12.06: the evidence bundle is complete, current and reproducible.

    python source-code/acceptance/verify_bundle.py        # after build_bundle.py

Checks the index against the directory it indexes: every artifact is indexed and no indexed artifact is missing, every SHA-256 still matches (so nothing was changed after the index was made), every
artifact says how to regenerate it and where that can run, the versions the runs depended on are recorded, what is not a pass is listed apart and explained by a defect, and the archive is
byte-for-byte the same when built twice. It writes its own evidence; the index files are the only ones it does not hash (an index cannot hold its own hash).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from acceptance import build_bundle as bb  # noqa: E402
from acceptance import defects as reg  # noqa: E402
from backend.evidence import Evidence  # noqa: E402

# evidence that records a failure on purpose, and the defect that explains it
EXPLAINED = {"p10_09_incident_quality": "F-08", "p11_06_target_load_first_run": "D-04"}


def main() -> int:
    ev = Evidence("P12.06", "p12_06_evidence_bundle", docs_name="p12_06_evidence_bundle")
    index = json.loads((bb.EVIDENCE / "p12_06_evidence_index.json").read_text(encoding="utf-8"))
    on_disk = {
        f"docs/evidence/{p.name}": p
        for p in bb.EVIDENCE.iterdir()
        if p.is_file() and p.name not in bb.SELF
    }
    indexed = {e["file"]: e for e in index["entries"]}
    ev.check(
        "every_file_under_docs_evidence_is_indexed_and_no_indexed_file_is_missing",
        set(on_disk) == set(indexed),
        f"{len(on_disk)} on disk, {len(indexed)} indexed; unindexed {sorted(set(on_disk) - set(indexed))[:4]}; missing {sorted(set(indexed) - set(on_disk))[:4]}",
    )
    changed = [
        f
        for f, p in on_disk.items()
        if f in indexed and hashlib.sha256(p.read_bytes()).hexdigest() != indexed[f]["sha256"]
    ]
    ev.check(
        "every_indexed_sha256_still_matches_the_file_nothing_changed_after_the_index_was_made",
        not changed,
        f"changed since indexing: {changed[:5]}",
    )
    manifest = "\n".join(f"{e['sha256']}  {e['file']}" for e in index["entries"])
    ev.check(
        "the_manifest_hash_is_the_hash_of_the_listed_hashes",
        hashlib.sha256(manifest.encode("utf-8")).hexdigest() == index["manifest_sha256"],
        index["manifest_sha256"][:16],
    )
    unrecorded = [
        e["file"]
        for e in index["entries"]
        if e["kind"] == "evidence"
        and (e["command"] == "not recorded" or e["conditions"] == "not recorded")
    ]
    ev.check(
        "every_evidence_file_says_how_to_regenerate_it_and_under_what_conditions",
        not unrecorded,
        f"not recorded: {unrecorded[:6]}",
    )
    unlinked = [
        e["file"]
        for e in index["entries"]
        if e["kind"] == "evidence"
        and not e["cited_by"]
        and not e["file"].rsplit("/", 1)[1].startswith(("p12_", "p11_08", "p09_0", "p11_0"))
    ]
    ev.metrics["evidence_not_cited_by_any_matrix_row"] = unlinked
    versions = index["versions"]
    ev.check(
        "the_versions_the_runs_depended_on_are_recorded",
        bool(
            versions["python"]
            and versions["git_commit"]
            and sum(1 for v in versions["packages"].values() if v) >= 10
        ),
        f"python {versions['python']}, {sum(1 for v in versions['packages'].values() if v)} of {len(versions['packages'])} packages, commit {str(versions['git_commit'])[:10]}, node {versions['node']}, docker {versions['docker_in_wsl']}",
    )
    failing = index["records_a_failure_by_design"]
    unexplained = [
        f
        for f in failing
        if f.rsplit("/", 1)[1][:-5] not in EXPLAINED
        or EXPLAINED[f.rsplit("/", 1)[1][:-5]] not in {d.id for d in reg.DEFECTS}
    ]
    ev.check(
        "every_artifact_that_records_a_failure_is_explained_by_a_defect_in_the_register",
        not unexplained,
        f"records a failure: {failing}; unexplained: {unexplained}",
    )
    sweep = index["sweep"]
    ev.check(
        "no_run_of_the_sweep_is_left_failing",
        not sweep.get("failed"),
        f"{len(sweep.get('passed', []))} passed, failed: {[f['id'] for f in sweep.get('failed', [])]}",
    )
    ev.check(
        "what_was_not_run_and_what_was_not_re_run_is_listed_not_hidden",
        "not_run" in sweep and isinstance(index["passed_but_not_re_run_in_this_phase"], list),
        f"never run by the sweep: {sweep.get('not_run')}; passed but older than the sweep: {len(index['passed_but_not_re_run_in_this_phase'])}",
    )
    digest_file = bb.OUTPUT / "evidence-bundle.sha256"
    lines = digest_file.read_text(encoding="utf-8").splitlines() if digest_file.exists() else []
    same = len(lines) == 2 and lines[0].split()[0] == lines[1].split()[0]
    ev.check(
        "the_archive_is_reproducible_two_builds_give_the_same_bytes",
        same,
        lines[0][:40] if lines else "no archive record",
    )
    archive = next(
        iter(sorted(bb.OUTPUT.glob(f"evidence-bundle-{index['manifest_sha256'][:12]}.tar.gz"))),
        None,
    )
    ev.check(
        "the_archive_for_this_manifest_exists_and_its_hash_is_the_recorded_one",
        bool(
            archive
            and lines
            and hashlib.sha256(archive.read_bytes()).hexdigest() == lines[0].split()[0]
        ),
        archive.name if archive else "missing",
    )
    ev.metrics |= {
        "artifacts": len(index["entries"]),
        "manifest_sha256": index["manifest_sha256"],
        "archive": archive.name if archive else None,
        "archive_sha256": lines[0].split()[0] if lines else None,
        "status_counts": {
            s: sum(1 for e in index["entries"] if e["status"] == s)
            for s in {e["status"] for e in index["entries"]}
        },
    }
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
