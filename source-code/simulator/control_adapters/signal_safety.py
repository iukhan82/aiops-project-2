"""P07.10: an independent, runtime signal-safety monitor (stdlib only, so the
same file runs inside the SUMO container and in host unit tests).

It exists because "safe by construction" is a claim about a mechanism, not a
measurement of what happened. The monitor never looks at how a phase change
was requested; it reads the raw red/yellow/green state string of every
controlled intersection at every simulation step and checks the outcome
against the network's own design:

* `conflicting_green` - two protected greens (`G`) at once on links the
  junction's own `<request foes=...>` table marks as conflicting. Pedestrian
  crossings are links in that table too, so a vehicle `G` against a crossing
  `G` is caught here. Permissive greens (`g`) are the network's stated
  yield-to-conflicting-traffic convention and are not counted.
* `no_yellow_before_red` / `short_yellow` - a vehicle link may not go from
  green to red without a yellow, and the yellow must last at least the
  shortest yellow the network's own programs use.
* `short_pedestrian_green` - a crossing's green must last at least the
  shortest pedestrian green the network's own programs use.
* `short_pedestrian_clearance` - after a crossing leaves green, a conflicting
  vehicle link may not turn green sooner than the shortest such gap the
  network's own programs use.

Design minimums are *derived from the network's static programs*, not
hard-coded, and `tests/test_signal_safety.py` replays each program's own cycle
through the monitor to show it raises no false alarm on the unmodified design.
Observation starts from the first state seen (nothing before it is judged), so
a scenario's own initial setup is not mistaken for a violation.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TlDesign:
    tl_id: str
    n_links: int
    crossings: set[int]
    foes: dict[int, set[int]]
    phases: list[tuple[str, float, float | None]]  # (state, duration_s, minDur_s or None)


@dataclass
class Design:
    tls: dict[str, TlDesign]
    min_yellow_s: float
    min_ped_green_s: float
    min_ped_clearance_s: float

    def thresholds(self) -> dict:
        return {
            "min_yellow_s": self.min_yellow_s,
            "min_ped_green_s": self.min_ped_green_s,
            "min_ped_clearance_s": self.min_ped_clearance_s,
        }


def _lower_bound(duration: float, min_dur: float | None) -> float:
    """An actuated phase is at least its minDur; a fixed phase is exactly its duration."""
    return min_dur if min_dur is not None else duration


def _min_ped_green(tl: TlDesign) -> float | None:
    best: float | None = None
    n = len(tl.phases)
    for c in tl.crossings:
        for start in range(n):
            state_prev, state = tl.phases[(start - 1) % n][0], tl.phases[start][0]
            if state[c] == "G" and state_prev[c] != "G":
                run, k = 0.0, start
                while tl.phases[k % n][0][c] == "G" and k - start < n:
                    run += _lower_bound(tl.phases[k % n][1], tl.phases[k % n][2])
                    k += 1
                best = run if best is None else min(best, run)
    return best


def _min_ped_clearance(tl: TlDesign) -> float | None:
    best: float | None = None
    n = len(tl.phases)
    for c in tl.crossings:
        conflicting = {f for f in tl.foes.get(c, set()) if f not in tl.crossings} | {
            f for f in range(tl.n_links) if c in tl.foes.get(f, set()) and f not in tl.crossings
        }
        for start in range(n):
            if not (tl.phases[start][0][c] == "G" and tl.phases[(start + 1) % n][0][c] != "G"):
                continue
            for f in conflicting:
                gap = 0.0
                for k in range(1, n + 1):
                    phase_state = tl.phases[(start + k) % n][0]
                    if phase_state[f] == "G" and tl.phases[(start + k - 1) % n][0][f] != "G":
                        best = gap if best is None else min(best, gap)
                        break
                    gap += _lower_bound(
                        tl.phases[(start + k) % n][1], tl.phases[(start + k) % n][2]
                    )
    return best


def load_design(net_file: Path) -> Design:
    root = ET.parse(net_file).getroot()
    foes_by_junction: dict[str, dict[int, set[int]]] = {}
    for junction in root.findall("junction"):
        requests = junction.findall("request")
        if not requests:
            continue
        n = len(requests)
        foes_by_junction[junction.get("id")] = {
            int(r.get("index")): {n - 1 - pos for pos, ch in enumerate(r.get("foes")) if ch == "1"}
            for r in requests
        }
    crossing_links: dict[str, set[int]] = {}
    for conn in root.findall("connection"):
        tl, source = conn.get("tl"), conn.get("from", "")
        if tl and source.startswith(":") and "_w" in source:
            crossing_links.setdefault(tl, set()).add(int(conn.get("linkIndex")))
    tls: dict[str, TlDesign] = {}
    yellows: list[float] = []
    greens: list[float] = []
    clearances: list[float] = []
    for logic in root.findall("tlLogic"):
        tl_id = logic.get("id")
        phases = [
            (
                p.get("state"),
                float(p.get("duration")),
                float(p.get("minDur")) if p.get("minDur") else None,
            )
            for p in logic.findall("phase")
        ]
        design = TlDesign(
            tl_id,
            len(phases[0][0]),
            crossing_links.get(tl_id, set()),
            foes_by_junction.get(tl_id, {}),
            phases,
        )
        tls[tl_id] = design
        yellows += [d for s, d, _ in phases if "y" in s]
        if (g := _min_ped_green(design)) is not None:
            greens.append(g)
        if (c := _min_ped_clearance(design)) is not None:
            clearances.append(c)
    return Design(tls, min(yellows), min(greens), min(clearances))


@dataclass
class Violation:
    kind: str
    tl_id: str
    t: float
    detail: str

    def __str__(self) -> str:
        return f"{self.kind} at {self.tl_id} t={self.t}: {self.detail}"


@dataclass
class SignalSafetyMonitor:
    design: Design
    violations: list[Violation] = field(default_factory=list)
    observations: int = 0
    _last: dict[str, str] = field(default_factory=dict)
    _yellow_start: dict[tuple[str, int], float] = field(default_factory=dict)
    _ped_green_start: dict[tuple[str, int], float] = field(default_factory=dict)
    _ped_left: dict[tuple[str, int], float] = field(default_factory=dict)

    def _flag(self, kind: str, tl: str, t: float, detail: str) -> None:
        self.violations.append(Violation(kind, tl, t, detail))

    def observe(self, t: float, states: dict[str, str]) -> None:
        eps = 1e-6
        for tl_id, state in states.items():
            d = self.design.tls.get(tl_id)
            if d is None:
                continue
            self.observations += 1
            for i, ch in enumerate(state):
                if ch == "G":
                    for j in d.foes.get(i, ()):
                        if j > i and j < len(state) and state[j] == "G":
                            self._flag(
                                "conflicting_green",
                                tl_id,
                                t,
                                f"links {i} and {j} are both G in {state}",
                            )
            prev = self._last.get(tl_id)
            if prev is not None:
                for i, (a, b) in enumerate(zip(prev, state, strict=False)):
                    if a == b:
                        continue
                    key = (tl_id, i)
                    if i in d.crossings:
                        if b == "G":
                            self._ped_green_start[key] = t
                        if a == "G" and b != "G":
                            started = self._ped_green_start.get(key)
                            if (
                                started is not None
                                and t - started < self.design.min_ped_green_s - eps
                            ):
                                self._flag(
                                    "short_pedestrian_green",
                                    tl_id,
                                    t,
                                    f"crossing {i} was green for {t - started}s (design minimum {self.design.min_ped_green_s}s)",
                                )
                            self._ped_left[key] = t
                        continue
                    if b == "y":
                        self._yellow_start[key] = t
                    if a in "Gg" and b == "r":
                        self._flag(
                            "no_yellow_before_red", tl_id, t, f"link {i} went {a}->r directly"
                        )
                    if a == "y" and b == "r":
                        started = self._yellow_start.get(key)
                        if started is not None and t - started < self.design.min_yellow_s - eps:
                            self._flag(
                                "short_yellow",
                                tl_id,
                                t,
                                f"link {i} yellow lasted {t - started}s (design minimum {self.design.min_yellow_s}s)",
                            )
                    if b == "G":
                        for c in d.crossings:
                            if i in d.foes.get(c, set()) or c in d.foes.get(i, set()):
                                left = self._ped_left.get((tl_id, c))
                                if (
                                    left is not None
                                    and t - left < self.design.min_ped_clearance_s - eps
                                ):
                                    self._flag(
                                        "short_pedestrian_clearance",
                                        tl_id,
                                        t,
                                        f"link {i} turned green {t - left}s after crossing {c} ended (design minimum {self.design.min_ped_clearance_s}s)",
                                    )
            self._last[tl_id] = state

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for v in self.violations:
            kinds[v.kind] = kinds.get(v.kind, 0) + 1
        return {
            "observations": self.observations,
            "violation_count": len(self.violations),
            "violations_by_kind": kinds,
            "examples": [str(v) for v in self.violations[:5]],
            "design": self.design.thresholds(),
        }
