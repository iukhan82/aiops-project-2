"""Phase 12: system acceptance and evidence.

`catalog.py` is the single description of what is run and what it must show: every verifier the platform has (the RUNS), the acceptance scenarios and roles
(P12.01), the faults (P12.02), the safety, privacy, accessibility and fairness rows (P12.04), and the assessment requirements (P12.07). `run_catalog.py` runs the
RUNS one after another against the live stack and records what happened. The `verify_*.py` files here read the evidence those runs (and the target runs of
Phase 11) produced and say, cell by cell, what passes, what is partial and what is missing.
"""
