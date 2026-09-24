# SUMO feasibility fixture

This generated 3x3 grid proves P01.06 only. It is not the 12-intersection district
required by P03.01.

From Ubuntu/WSL2 with Docker active:

```bash
./source-code/simulator/feasibility/run_container.sh
```

The wrapper pins the official Eclipse SUMO 1.27.1 image by digest. The run
regenerates network and demand inputs, executes the same fixed-seed simulation
twice, compares semantic FCD, induction-loop and extracted telemetry hashes,
extracts JSON Lines telemetry with explicit units and provenance, and fails if
replay differs. Semantic XML hashing excludes SUMO's generated-at comment. Output
is gitignored.
