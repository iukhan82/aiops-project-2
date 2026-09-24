# P03.07: run manifest, event output and replay

An immutable manifest over the JSON/event output already produced by
P03.03-P03.06's `build_and_verify.py` runs, plus a minimal ordered/
idempotent/duplicate-safe replay over the manifest's event streams - per
`docs/PROJECT_CONTEXT.md`'s engineering invariant ("Replay is ordered,
acknowledged, bounded, idempotent, and duplicate-safe").

Pure Python, no SUMO/Docker needed - it only reads files those stages
already wrote:

```bash
python source-code/simulator/manifest/build_and_verify.py
python -m pytest source-code/tests/test_run_manifest.py -q
```

## What "immutable manifest" means here

`build_manifest.py` indexes 12 specific files across the four stages
(`sensors`, `emergency`, `scenarios`, `faults`), records each one's sha256,
and computes `manifest_sha256` as a hash over the sorted `(path, sha256)`
pairs - so any single-byte change to any listed file, or any change to the
set of listed files, changes `manifest_sha256`. `verify_manifest` re-hashes
every file from disk and recomputes `manifest_sha256`; `build_and_verify.py`
calls it twice (once against the just-built manifest object, once against
the same manifest reloaded from the JSON file on disk) and asserts zero
problems both times.

This indexes each stage's `output/run-a/` as it currently stands - if you
want the manifest to describe a fresh run, regenerate the upstream stages
first (`sensors`/`emergency`/`scenarios`' `run_container.sh`, `faults`'
`build_and_verify.py`).

## What "replay produces equivalent accepted sequences" means here

`replay.py` merges the 4 event-stream files (`sensors/observations.jsonl`,
`emergency/avl_events.jsonl`, `scenarios/overlay_events.jsonl`,
`faults/fault_events.jsonl` - 1715 events total in the last verified run),
sorts them into one global chronological order, and accepts each one unless
its `event_id` was already accepted (duplicate-safe) or its
`observation_time` is earlier than the last accepted event for the same
`device_id` (ordered per device). `build_and_verify.py` then proves:

1. Replaying the same event set twice, independently, produces the exact
   same accepted-id sequence and the same `accepted_sha256` - deterministic
   replay.
2. Replaying the event set with every event duplicated (`events + events`)
   produces the *same* `accepted_sha256` and the *same* `accepted_count` as
   the single-copy replay, with every extra copy rejected as `"duplicate"` -
   idempotent, duplicate-safe replay, not merely "ran twice without
   crashing".

This is a simulator-layer proof of the replay *mechanism*; it is not the
platform's real ingestion path (that is Phase 05/07 backend work) - it
operates on already-recorded files, not a live stream.

## A real bug this caught

Building the manifest and replaying its merged events the first time found
4 real `event_id` collisions: P03.06's `model`/`service`/`storage` faults
all share one `aiops_agent` device, and each fault's local `seq=0`/`seq=1`
numbering reset per fault instead of continuing per device, so
`uuid5(namespace, f"{run_id}:{device_id}:{seq}")` collided across the three
faults' onset events (and again across their end events).
`source-code/simulator/faults/generate_fault_events.py` now tracks a single
global per-device sequence counter across all fault specs sharing a device,
the same pattern P03.04 already used for its AVL devices.
`source-code/tests/test_run_manifest.py`'s
`test_no_duplicate_event_ids_across_all_stages` pins this as a regression
guard.

## Reproduction

Last verified run (2026-09-18): 12 source files indexed (4 of them event
streams), manifest verified clean against disk (twice - fresh object and
reloaded-from-JSON), 1715 total events, 0 rejections on the clean replay,
replay deterministic and duplicate-safe under a full self-duplication.
6/6 host-side tests passed.

## Scope

Disjoint train/validation/test dataset splits are P03.08. Automated scenario
bound/invariant checks and documenting synthetic-data limitations are
P03.09.
