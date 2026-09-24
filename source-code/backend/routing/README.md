# P07.02 - fastest-safe route and ETA alternatives

`graph.py` (directed graph over real P03.01 segments), `live_state.py` (turns live
incidents + recent corridor KPIs into per-segment `EdgeState`), `router.py` (Dijkstra +
penalize-and-resolve alternatives), `route_service.py` (wiring), `GET /api/v1/routes`,
`verify_routing.py`.

**Closures vs hazards, both from real live evidence, nothing fabricated.** A segment
under an active `collision`/`stalled_vehicle`/`wrong_way`/`flooding` incident at
high/critical severity is **closed**: the router will never route across it, however
much faster it looks, and still finds a way through when the grid has one. A segment
under `congestion`/`spillback`/`low_visibility`/a conflict indicator, or a cause at
lower severity, is a **hazard**: heavily penalized in the search so a comparable
detour wins, but still passable and labelled (`through_hazard:<kind> on <element>`) -
an emergency vehicle sometimes has no alternative and must be able to proceed through
it deliberately rather than being silently redirected.

**Travel time and uncertainty are grounded in what has actually been measured.** A
segment inherits its corridor's own recent KPI window (P06.01, `corridor_kpis`,
< 15 min old - anything older is treated as not-live, the same "never silently use
stale evidence as fresh" discipline SAFE-03 asks for elsewhere) distributed across its
segments by free-flow share (the KPI is corridor-wide; this is the finest granularity
it actually measures, stated as an assumption). Uncertainty uses the segment's own
measured `buffer_index` (planning-time reliability margin) when a live KPI exists,
otherwise a stated default planning margin (15%) - never a precise-looking invented
number. The reported ETA always uses the real travel time; only the *search* cost
inflates a hazard, so a route through a hazard never shows a fictional ETA.

Alternatives use the standard penalize-and-resolve method (solve, penalize the kept
route's own segments, resolve), discarding a candidate that shares more than 60% of its
segments with one already kept - a materially different route, not the same path with
one detour.

`verify_routing.py` (real Postgres, 11 checks, on the real 26-segment seeded network):
a baseline `int-a1`->`int-c4` route with 3 genuinely different alternatives; a real
high-severity incident (through P06.07's own incident repository) closes its segment
and the grid reroutes around it; a medium-severity congestion incident is a hazard, not
a closure; a synthetic recent corridor KPI (labelled, removed after) measurably changes
the reported travel time and uncertainty, and a KPI outside the live budget does not;
the API serves contract-shaped (`emergency-unit-assignment/v1`'s `route_alternatives`
shape) alternatives and 404s on an unknown node. Unit tests: `tests/test_routing.py`
(11, on a small hand-built graph with analytically known answers). Evidence:
`docs/evidence/p07_02_routing.json`.
