"""Realized post-encroachment time (PET) between road users, from the clean
full-rate SUMO trajectories in `tracks.csv.gz`.

PET is what actually happened, not a prediction: two road users' actual paths
cross at a point, and PET is the time between the first user leaving that
point and the second arriving (here |t_a - t_b| of the two crossing times).
It is the standard surrogate for a near miss. This is the *ground truth* the
edge indicator (edge/vru_conflict.py, which only ever sees noisy 1 Hz tracks
and predicts) is scored against; nothing here feeds a detector.

Only vehicle-vs-VRU pairs inside the same intersection site are considered;
paths that never cross (a pedestrian walking beside a passing car) are not a
conflict, however close, because no collision course exists.
"""

from __future__ import annotations

import csv
import gzip
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MAX_GAP_S = 0.6  # a longer hole in an object's rows means it left the site and came back
PET_MAX_S = 5.0  # interactions above this are not recorded
SEVERE_PET_S = 1.5
SERIOUS_PET_S = 3.0
# Risk weighting. At PET <= 3 s alone the large majority of interactions in the
# validation runs are vehicles crawling below 4 m/s through queues and the
# junction: routine yield-and-go, not a near miss (and a car at 4 m/s is far
# less lethal to a pedestrian than one at 10). A conflict therefore needs
# PET <= 3 s AND a vehicle at least this fast at the crossing point. Chosen after
# inspecting the validation PET/speed distribution - the plain-PET numbers are
# reported too.
CONFLICT_MIN_VEHICLE_SPEED_M_S = 4.0
TIME_SLACK_S = 6.0


@dataclass
class Piece:
    """One object's continuous stay inside one site."""

    obj_id: str
    cls: str
    sub: str
    site: str
    t: np.ndarray
    xy: np.ndarray
    speed: np.ndarray

    @property
    def t0(self) -> float:
        return float(self.t[0])

    @property
    def t1(self) -> float:
        return float(self.t[-1])


@dataclass(frozen=True)
class Interaction:
    site: str
    vru_id: str
    vru_cls: str
    vru_sub: str
    veh_id: str
    veh_sub: str
    t_vru: float
    t_veh: float
    pet_s: float
    x: float
    y: float
    vehicle_speed_m_s: float
    vru_speed_m_s: float

    @property
    def t_first(self) -> float:
        return min(self.t_vru, self.t_veh)

    @property
    def is_conflict(self) -> bool:
        return (
            self.pet_s <= SERIOUS_PET_S and self.vehicle_speed_m_s >= CONFLICT_MIN_VEHICLE_SPEED_M_S
        )

    @property
    def severity(self) -> str:
        if self.pet_s > SERIOUS_PET_S:
            return "mild"
        if self.vehicle_speed_m_s < CONFLICT_MIN_VEHICLE_SPEED_M_S:
            return "low_speed"
        return "severe" if self.pet_s <= SEVERE_PET_S else "serious"

    @property
    def vru_first(self) -> bool:
        return self.t_vru <= self.t_veh


def load_pieces(tracks_path: Path) -> list[Piece]:
    rows: dict[tuple[str, str], list[tuple[float, float, float, float]]] = defaultdict(list)
    meta: dict[tuple[str, str], tuple[str, str]] = {}
    with gzip.open(tracks_path, "rt", encoding="utf-8", newline="") as stream:
        for r in csv.DictReader(stream):
            key = (r["id"], r["site"])
            rows[key].append((float(r["t"]), float(r["x"]), float(r["y"]), float(r["speed"])))
            meta[key] = (r["cls"], r["sub"])
    pieces: list[Piece] = []
    for (obj_id, site), data in rows.items():
        data.sort()
        arr = np.asarray(data)
        cuts = np.flatnonzero(np.diff(arr[:, 0]) > MAX_GAP_S) + 1
        for chunk in np.split(arr, cuts):
            if len(chunk) >= 2:
                cls, sub = meta[(obj_id, site)]
                pieces.append(
                    Piece(obj_id, cls, sub, site, chunk[:, 0], chunk[:, 1:3], chunk[:, 3])
                )
    return pieces


def _crossings(a: Piece, b: Piece):
    """All (t_a, t_b, x, y, speed_a, speed_b) where a's and b's polylines cross."""
    p, r = a.xy[:-1], a.xy[1:] - a.xy[:-1]
    q, s = b.xy[:-1], b.xy[1:] - b.xy[:-1]
    denom = r[:, None, 0] * s[None, :, 1] - r[:, None, 1] * s[None, :, 0]
    dq = q[None, :, :] - p[:, None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = (
            dq[..., 0] * s[None, :, 1] - dq[..., 1] * s[None, :, 0]
        ) / denom  # position along a's segment
        v = (
            dq[..., 0] * r[:, None, 1] - dq[..., 1] * r[:, None, 0]
        ) / denom  # position along b's segment
    hit = (np.abs(denom) > 1e-9) & (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
    for i, j in zip(*np.nonzero(hit), strict=True):
        t_a = a.t[i] + u[i, j] * (a.t[i + 1] - a.t[i])
        t_b = b.t[j] + v[i, j] * (b.t[j + 1] - b.t[j])
        point = p[i] + u[i, j] * r[i]
        sp_a = a.speed[i] + u[i, j] * (a.speed[i + 1] - a.speed[i])
        sp_b = b.speed[j] + v[i, j] * (b.speed[j + 1] - b.speed[j])
        yield float(t_a), float(t_b), float(point[0]), float(point[1]), float(sp_a), float(sp_b)


def realized_interactions(pieces: list[Piece]) -> list[Interaction]:
    """One record per (VRU, vehicle, site): the crossing with the smallest PET, if <= PET_MAX_S."""
    by_site: dict[str, tuple[list[Piece], list[Piece]]] = defaultdict(lambda: ([], []))
    for piece in pieces:
        by_site[piece.site][0 if piece.cls in ("ped", "cyc") else 1].append(piece)
    out: list[Interaction] = []
    for site, (vrus, vehicles) in by_site.items():
        vehicles.sort(key=lambda p: p.t0)
        starts = np.array([v.t0 for v in vehicles])
        for vru in vrus:
            lo = np.searchsorted(
                starts, vru.t0 - 120.0
            )  # vehicles rarely dwell in a site longer than this
            hi = np.searchsorted(starts, vru.t1 + TIME_SLACK_S)
            for veh in vehicles[lo:hi]:
                if veh.t1 + TIME_SLACK_S < vru.t0 or veh.t0 - TIME_SLACK_S > vru.t1:
                    continue
                if (
                    vru.xy[:, 0].max() + 1 < veh.xy[:, 0].min()
                    or veh.xy[:, 0].max() + 1 < vru.xy[:, 0].min()
                    or vru.xy[:, 1].max() + 1 < veh.xy[:, 1].min()
                    or veh.xy[:, 1].max() + 1 < vru.xy[:, 1].min()
                ):
                    continue
                best = None
                for t_vru, t_veh, x, y, sp_vru, sp_veh in _crossings(vru, veh):
                    pet = abs(t_vru - t_veh)
                    if best is None or pet < best[0]:
                        best = (pet, t_vru, t_veh, x, y, sp_vru, sp_veh)
                if best is not None and best[0] <= PET_MAX_S:
                    pet, t_vru, t_veh, x, y, sp_vru, sp_veh = best
                    out.append(
                        Interaction(
                            site,
                            vru.obj_id,
                            vru.cls,
                            vru.sub,
                            veh.obj_id,
                            veh.sub,
                            round(t_vru, 3),
                            round(t_veh, 3),
                            round(pet, 3),
                            round(x, 2),
                            round(y, 2),
                            round(sp_veh, 2),
                            round(sp_vru, 2),
                        )
                    )
    out.sort(key=lambda i: (i.t_first, i.site, i.vru_id, i.veh_id))
    return out
