"""P08.02: generates the editable SVG wireframes in this directory.

    python docs/ux/wireframes/build_wireframes.py

Each output is a plain SVG (open and edit in Figma, Inkscape or a text editor):
every region is a named `<g id="region-...">`, every numbered callout is a
`<g id="callout-N">` whose meaning is in docs/ux/WIREFRAMES.md, and the file
carries a `<title>` and `<desc>`. The wireframes use the design system's dark
palette so contrast is judged on the real colours, but they are structure, not
final visual design: P08.03 fixes tokens and components.

Navigation and the signed-in identity are derived from the same
`inventory.json` the backend and frontend use, so a wireframe can never show a
role a menu entry the role does not hold.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent
INVENTORY = json.loads((OUT.parents[2] / "source-code" / "frontend" / "src" / "config" / "inventory.json").read_text(encoding="utf-8"))
NAV_GROUPS = [("Map", ["map"]), ("Analytics", ["analytics-corridors"]), ("Incidents", ["incidents"]), ("Dispatch", ["dispatch"]),
              ("Actions", ["actions-commands", "actions-recommendations", "actions-outcomes"]), ("Handover", ["handover"]), ("Operations", ["operations"]),
              ("Audit", ["audit"]), ("Demo", ["demo"])]
USERS = {"operator": "alex.chen", "supervisor": "sam.okafor", "dispatcher": "dana.rivera", "incident_commander": "eve.laurent", "field_responder": "fin.hassan",
         "auditor": "ana.petrov", "demo_operator": "dee.moreno"}

BG, CARD, MUTED, BORDER = "#0F172A", "#1B2336", "#272F42", "#475569"
FG, FG_MUTED, ACCENT, DANGER, WARN, INFO = "#F8FAFC", "#94A3B8", "#22C55E", "#EF4444", "#F59E0B", "#38BDF8"
FONT = "IBM Plex Sans, Inter, system-ui, sans-serif"
MONO = "IBM Plex Mono, ui-monospace, monospace"


def nav_items(role: str) -> list[str]:
    """Only what the role's capabilities allow - the same rule the frontend applies from the same inventory."""
    caps = {c for c, spec in INVENTORY["capabilities"].items() if role in spec["roles"]}
    screens = {s["id"]: s for s in INVENTORY["screens"]}
    return [label for label, ids in NAV_GROUPS if any(screens[i]["capability"] in caps for i in ids)]


class Svg:
    def __init__(self, name: str, w: int, h: int, title: str, desc: str, role: str, screens: tuple[str, ...]):
        self.name, self.w, self.h, self.title, self.desc, self.role, self.screens = name, w, h, title, desc, role, screens
        self.parts: list[str] = []
        self.callouts: list[tuple[int, str]] = []

    def raw(self, s: str) -> None:
        self.parts.append(s)

    def group(self, gid: str, label: str):
        return _Group(self, gid, label)

    def rect(self, x, y, w, h, fill=CARD, stroke=BORDER, rx=6, sw=1, dash=None) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.raw(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def text(self, x, y, s, size=14, fill=FG, weight=400, anchor="start", mono=False) -> None:
        fam = MONO if mono else FONT
        self.raw(f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(str(s))}</text>')

    def line(self, x1, y1, x2, y2, stroke=BORDER, sw=1, dash=None) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.raw(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def circle(self, cx, cy, r, fill=CARD, stroke=BORDER, sw=1) -> None:
        self.raw(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

    def status(self, x, y, label, kind, size=13) -> None:
        """Status is never colour alone: a distinct shape AND the word."""
        colour = {"ok": ACCENT, "warn": WARN, "bad": DANGER, "info": INFO, "muted": FG_MUTED}[kind]
        shape = {
            "ok": f'<circle cx="{x + 6}" cy="{y - 5}" r="5" fill="{colour}"/>',
            "warn": f'<polygon points="{x + 6},{y - 11} {x + 12},{y} {x},{y}" fill="{colour}"/>',
            "bad": f'<rect x="{x}" y="{y - 11}" width="11" height="11" fill="{colour}"/>',
            "info": f'<polygon points="{x + 6},{y - 12} {x + 12},{y - 6} {x + 6},{y} {x},{y - 6}" fill="{colour}"/>',
            "muted": f'<circle cx="{x + 6}" cy="{y - 5}" r="5" fill="none" stroke="{colour}" stroke-width="2"/>',
        }[kind]
        self.raw(shape)
        self.text(x + 18, y, label, size, FG)

    def button(self, x, y, w, h, label, primary=False, size=14, disabled=False) -> None:
        fill = ACCENT if primary else "none"
        stroke = ACCENT if primary else FG_MUTED
        color = "#0F172A" if primary else FG
        dash = "4 3" if disabled else None
        self.raw('<g class="control">')
        self.rect(x, y, w, h, fill, stroke, 8, 2, dash)
        self.text(x + w / 2, y + h / 2 + size / 3, label, size, color, 600, "middle")
        self.raw("</g>")

    def callout(self, n: int, x: int, y: int, meaning: str) -> None:
        """Drawn last, on the top-left corner of the thing it explains, so it never covers content."""
        self.callouts.append((n, meaning))
        self.raw(f'<g id="callout-{n}"><circle cx="{x}" cy="{y}" r="11" fill="{WARN}" stroke="#0F172A" stroke-width="2"/>'
                 f'<text x="{x}" y="{y + 5}" font-family="{FONT}" font-size="13" font-weight="700" fill="#0F172A" text-anchor="middle">{n}</text></g>')

    def save(self) -> Path:
        body = "\n".join(self.parts)
        svg = (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" '
               f'role="img" aria-labelledby="t d" data-role="{self.role}">\n'
               f'<title id="t">{escape(self.title)}</title>\n<desc id="d">{escape(self.desc)}</desc>\n<rect width="{self.w}" height="{self.h}" fill="{BG}"/>\n{body}\n</svg>\n')
        path = OUT / f"{self.name}.svg"
        path.write_text(svg, encoding="utf-8")
        return path


class _Group:
    def __init__(self, svg: Svg, gid: str, label: str):
        self.svg, self.gid, self.label = svg, gid, label

    def __enter__(self):
        self.svg.raw(f'<g id="{self.gid}" data-region="{escape(self.label)}">')
        return self.svg

    def __exit__(self, *exc):
        self.svg.raw("</g>")


def header(s: Svg, w: int, h: int = 48, live: str = "Connected - updated 2 s ago", scale: float = 1.0) -> None:
    fs = int(14 * scale)
    with s.group("region-header", "banner"):
        s.rect(0, 0, w, h, CARD, BORDER, 0)
        s.rect(12, int(h / 2 - 14), 28, 28, ACCENT, ACCENT, 6)
        s.text(52, h / 2 + 6, "Traffic Operations", int(17 * scale), FG, 700)
        s.status(int(w * 0.34), int(h / 2 + 5), live, "ok", fs)
        s.rect(int(w * 0.58), int(h / 2 - 12), 92, 24, MUTED, INFO, 12, 1)
        s.text(int(w * 0.58) + 46, int(h / 2 + 5), "SIMULATED", 12, INFO, 700, "middle")
        s.text(w - 56, h / 2 + 5, f"{USERS[s.role]} - {s.role}", fs, FG_MUTED, 400, "end")
        s.rect(w - 44, int(h / 2 - 14), 32, 28, "none", FG_MUTED, 6, 2)
        s.text(w - 28, int(h / 2 + 6), "v", 14, FG, 600, "middle")


def nav(s: Svg, x: int, y: int, w: int, h: int, active: str) -> None:
    items = nav_items(s.role)
    assert active in items, (s.role, active, items)
    with s.group("region-nav", "navigation"):
        s.rect(x, y, w, h, CARD, BORDER, 0)
        s.text(x + 16, y + 26, f"Signed in as {s.role}", 12, FG_MUTED)
        for i, item in enumerate(items):
            yy = y + 48 + i * 40
            is_active = item == active
            s.rect(x + 8, yy, w - 16, 34, MUTED if is_active else "none", ACCENT if is_active else "none", 6, 2)
            s.text(x + 24, yy + 22, item, 14, FG, 600 if is_active else 400)
            if is_active:
                s.text(x + w - 28, yy + 22, "current", 10, FG_MUTED, 400, "end")


def schematic(s: Svg, x: int, y: int, w: int, h: int, detail: bool = True, incident: bool = True) -> None:
    """The 3-corridor, 12-intersection district drawn schematically. Congestion is shown by line style AND a text
    label, never by colour alone: solid = flowing, dashed = slow, double line = closed."""
    with s.group("region-map", "map canvas"):
        s.rect(x, y, w, h, "#0B1220", BORDER, 8, 1)
        cols = [x + w * (0.12 + 0.26 * i) for i in range(4)]
        rows = [y + h * (0.22 + 0.28 * j) for j in range(3)]
        names = "ABC"
        for j, ry in enumerate(rows):
            for i in range(3):
                dashed = (j == 1 and i == 1)
                s.line(cols[i], ry, cols[i + 1], ry, WARN if dashed else FG_MUTED, 4, "10 6" if dashed else None)
                if detail and (j, i) in ((1, 1), (0, 0)):
                    label = "slow 18 km/h" if dashed else "flowing 46 km/h"
                    s.text((cols[i] + cols[i + 1]) / 2, ry - 14, label, 11, WARN if dashed else FG_MUTED, 400, "middle")
        for i in (0, 3):
            for j in range(2):
                s.line(cols[i], rows[j], cols[i], rows[j + 1], FG_MUTED, 3)
        for j, ry in enumerate(rows):
            for i, cx in enumerate(cols):
                s.circle(cx, ry, 9, CARD, FG, 2)
                if detail:
                    s.text(cx + 14, ry + 22, f"{names[j]}{i + 1}", 11, FG_MUTED, 400, "start")
        if incident:
            ix, iy = (cols[1] + cols[2]) / 2, rows[1]
            s.raw(f'<polygon points="{ix},{iy - 16} {ix + 14},{iy + 10} {ix - 14},{iy + 10}" fill="{DANGER}" stroke="{FG}" stroke-width="2"/>')
            if detail:
                s.text(ix, iy + 34, "Incident: stalled vehicle", 11, FG, 700, "middle")
        s.raw(f'<rect x="{cols[3] - 6}" y="{rows[0] - 6}" width="12" height="12" fill="{INFO}" stroke="{FG}"/>')
        if detail:
            s.text(cols[3] - 12, rows[0] - 14, "Unit E1", 11, INFO, 700, "end")


def write_index(pages: list[tuple[str, str]]) -> None:
    items = "\n".join(f'<section><h2>{escape(t)}</h2><img src="{n}.svg" alt="{escape(t)} wireframe" style="max-width:100%;border:1px solid #475569"/></section>' for n, t in pages)
    (OUT / "index.html").write_text(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>P08.02 wireframes</title>'
        f'<style>body{{background:#0F172A;color:#F8FAFC;font-family:{FONT};margin:24px}}section{{margin-bottom:32px}}h2{{font-size:18px}}</style></head>'
        f"<body><h1>P08.02 wireframes</h1><p>Editable SVG sources. Meaning of each numbered callout: docs/ux/WIREFRAMES.md.</p>{items}</body></html>\n", encoding="utf-8")


def view_toggle(s: Svg, x: int, y: int, active: str) -> None:
    with s.group("region-view-toggle", "view toggle"):
        s.rect(x, y, 130, 32, MUTED, BORDER, 8)
        ax = x + 2 if active == "Map" else x + 66
        s.rect(ax, y + 2, 62, 28, ACCENT, ACCENT, 6)
        s.text(x + 33, y + 21, "Map", 13, "#0F172A" if active == "Map" else FG, 700 if active == "Map" else 400, "middle")
        s.text(x + 97, y + 21, "List", 13, "#0F172A" if active == "List" else FG, 700 if active == "List" else 400, "middle")


# --------------------------------------------------------------------------------------------- map, laptop
def map_laptop() -> Svg:
    s = Svg("map-laptop", 1366, 768, "Live operations map - laptop (1366x768)",
            "Header with live-feed status, role navigation, a layers toolbar, a schematic map with legend, a replay bar, a selection panel and a critical incident queue, with a Map/List toggle.", "operator", ("map",))
    header(s, 1366)
    nav(s, 0, 48, 200, 720, "Map")
    with s.group("region-main", "main"):
        s.text(224, 84, "Live operations map", 20, FG, 700)
        view_toggle(s, 892, 60, "Map")
        with s.group("region-layers", "layers toolbar"):
            s.rect(216, 106, 810, 40, CARD, BORDER, 8)
            s.text(232, 131, "Layers", 13, FG, 700)
            for i, (lab, on) in enumerate([("Speed", True), ("Incidents", True), ("Signals", False), ("Devices", False), ("Emergency", True)]):
                xx = 306 + i * 134
                s.rect(xx, 118, 16, 16, ACCENT if on else "none", FG_MUTED, 3, 2)
                s.text(xx + 24, 131, lab, 13, FG)
        schematic(s, 216, 156, 810, 470)
        with s.group("region-legend", "legend"):
            s.rect(228, 548, 310, 66, CARD, BORDER, 8)
            s.line(240, 570, 268, 570, FG_MUTED, 4)
            s.text(274, 574, "flowing", 11, FG)
            s.line(336, 570, 364, 570, WARN, 4, "10 6")
            s.text(370, 574, "slow", 11, FG)
            s.line(410, 570, 438, 570, DANGER, 4)
            s.text(444, 574, "closed", 11, FG)
            s.text(240, 602, "Line style and label carry the meaning; colour is a hint.", 11, FG_MUTED)
        with s.group("region-replay", "replay and pause bar"):
            s.rect(216, 638, 810, 118, CARD, BORDER, 8)
            s.button(232, 654, 84, 34, "Pause", size=13)
            s.button(324, 654, 84, 34, "Live", primary=True, size=13)
            s.text(426, 676, "Replay: drag to a past time - clearly marked when not live", 13, FG_MUTED)
            s.line(240, 716, 1000, 716, BORDER, 4)
            s.circle(1000, 716, 9, ACCENT, FG, 2)
            s.text(240, 742, "-30 min", 11, FG_MUTED)
            s.text(1000, 742, "now", 11, FG_MUTED, 400, "end")
    with s.group("region-selection", "selection details"):
        s.rect(1046, 48, 320, 380, CARD, BORDER, 0)
        s.text(1062, 80, "Corridor B, eastbound", 16, FG, 700)
        s.status(1062, 108, "Slow", "warn")
        for i, (k, v) in enumerate([("Speed", "18 km/h"), ("Delay", "+74 s"), ("Queue", "0.34"), ("Observed", "09:14:52 UTC"), ("Truth label", "simulated"), ("Freshness", "fresh, 3 s")]):
            yy = 140 + i * 26
            s.text(1062, yy, k, 13, FG_MUTED)
            s.text(1350, yy, v, 13, FG, 600, "end", mono=True)
        s.button(1062, 304, 288, 40, "Open corridor analytics", size=14)
        s.button(1062, 356, 288, 40, "Show in list view", size=14)
    with s.group("region-critical-queue", "critical incident queue"):
        s.rect(1046, 428, 320, 340, CARD, BORDER, 0)
        s.text(1062, 460, "Critical incidents (2)", 15, FG, 700)
        for i, (t, st, kind) in enumerate([("Stalled vehicle, B2-B3", "Open, unacknowledged", "bad"), ("Congestion, A3-A4", "Acknowledged", "warn")]):
            yy = 478 + i * 92
            s.rect(1058, yy, 296, 80, MUTED, BORDER, 8)
            s.status(1070, yy + 26, "Critical" if i == 0 else "High", kind)
            s.text(1070, yy + 48, t, 13, FG, 600)
            s.text(1070, yy + 68, st, 12, FG_MUTED)
    s.callout(1, 202, 62, "Skip link and landmarks: banner, navigation, main, complementary. The first Tab stop is 'Skip to main content'.")
    s.callout(2, 470, 20, "Live-feed status: text plus shape, with the age of the newest value. Becomes 'Reconnecting - values may be older than shown' with the last connected time.")
    s.callout(3, 216, 106, "Layers toolbar: real checkboxes, keyboard operable; a hidden layer is announced.")
    s.callout(4, 216, 156, "Map canvas: focusable elements with labels; arrow keys pan, + and - zoom, Enter selects. Every fact here is also in the list view.")
    s.callout(5, 892, 60, "Map / List toggle: the list is a full equivalent, not a fallback of last resort.")
    s.callout(6, 228, 548, "Legend: line style and text label, not colour alone.")
    s.callout(7, 216, 638, "Pause and replay: Pause stops updates and says so in a banner; replay is labelled 'not live' everywhere.")
    s.callout(8, 1046, 48, "Selection panel: value, unit, observed time, truth label, freshness - the same set for every selectable thing.")
    s.callout(9, 1046, 428, "Critical incident queue: persistent, critical first, status as text plus shape.")
    return s


# --------------------------------------------------------------------------------------------- accessible list, laptop
def list_laptop() -> Svg:
    s = Svg("map-list-laptop", 1366, 768, "Live operations map - accessible list alternative (1366x768)",
            "A sortable, filterable table carrying every fact the map shows, with freshness and truth label per row and a details panel, synchronised with map selection.", "operator", ("map",))
    header(s, 1366)
    nav(s, 0, 48, 200, 720, "Map")
    with s.group("region-main", "main"):
        s.text(224, 84, "Live operations map - list view", 20, FG, 700)
        view_toggle(s, 892, 60, "List")
        with s.group("region-filters", "filters"):
            s.rect(216, 106, 810, 44, CARD, BORDER, 8)
            for i, lab in enumerate(["Type: all v", "Status: all v", "Freshness: all v", "Search elements..."]):
                s.rect(228 + i * 196, 114, 184, 28, "none", FG_MUTED, 6, 1)
                s.text(240 + i * 196, 133, lab, 12, FG_MUTED)
        with s.group("region-table", "data table"):
            cols = [("Element", 216), ("Type", 412), ("Status", 508), ("Metric", 658), ("Freshness", 768), ("Truth", 888)]
            s.rect(216, 158, 810, 480, CARD, BORDER, 8)
            s.rect(216, 158, 810, 34, MUTED, BORDER, 8)
            for name, x in cols:
                s.text(x + 12, 180, name + (" ^" if name == "Status" else ""), 13, FG, 700)
            rows = [("Corridor B east", "Corridor", "Slow", "warn", "18 km/h", "fresh 3 s", "simulated"), ("Stalled vehicle B2-B3", "Incident", "Critical", "bad", "open", "fresh 9 s", "inferred"),
                    ("Signal int-b2", "Signal", "Normal", "ok", "phase 0", "fresh 2 s", "simulated"), ("Loop det-b2-04", "Device", "Silent", "muted", "-", "stale 4 min", "measured"),
                    ("Corridor A east", "Corridor", "Flowing", "ok", "46 km/h", "fresh 3 s", "simulated"), ("Unit E1 (engine)", "Emergency", "En route", "info", "ETA 1:42", "fresh 5 s", "predicted"),
                    ("Camera cam-c3-01", "Device", "Flowing", "ok", "-", "fresh 6 s", "measured"), ("Signal int-c3", "Signal", "Normal", "ok", "phase 2", "fresh 2 s", "simulated")]
            for i, (a, b, c, kind, m, f, t) in enumerate(rows):
                yy = 218 + i * 52
                s.line(216, yy - 22, 1026, yy - 22, BORDER, 1)
                if i == 1:
                    s.rect(224, yy - 20, 794, 46, ACCENT + "22", ACCENT, 6, 2)
                s.text(228, yy + 4, a, 13, FG, 600)
                s.text(424, yy + 4, b, 13, FG)
                s.status(520, yy + 4, c, kind)
                s.text(670, yy + 4, m, 13, FG, 400, mono=True)
                s.text(780, yy + 4, f, 13, FG_MUTED if "stale" in f else FG)
                s.text(900, yy + 4, t, 13, FG)
            s.text(228, 626, "Showing 8 of 141 - columns sortable by keyboard (Enter or Space on the header)", 12, FG_MUTED)
        with s.group("region-live-log", "polite live region"):
            s.rect(216, 650, 810, 96, CARD, BORDER, 8)
            s.text(232, 676, "Updates (announced politely, never stealing focus)", 13, FG, 700)
            s.text(232, 700, "09:14:52  Stalled vehicle B2-B3 became Critical", 12, FG_MUTED)
            s.text(232, 720, "09:14:47  Corridor B east slowed to 18 km/h", 12, FG_MUTED)
            s.button(846, 664, 168, 30, "Pause updates", size=12)
    with s.group("region-selection", "selection details"):
        s.rect(1046, 48, 320, 720, CARD, BORDER, 0)
        s.text(1062, 80, "Stalled vehicle, B2-B3", 16, FG, 700)
        s.status(1062, 108, "Critical, open", "bad")
        for i, (k, v) in enumerate([("Severity", "critical"), ("Confidence", "0.63 (inferred)"), ("Opened", "09:12:07 UTC"), ("Owner", "OPS"), ("Freshness", "fresh, 9 s")]):
            yy = 140 + i * 26
            s.text(1062, yy, k, 13, FG_MUTED)
            s.text(1350, yy, v, 13, FG, 600, "end", mono=True)
        s.button(1062, 294, 288, 40, "Show on map", size=14)
        s.button(1062, 346, 288, 40, "Open incident", primary=True, size=14)
    s.callout(1, 892, 60, "Same toggle, same page: choosing List keeps the selection and the filters.")
    s.callout(2, 216, 106, "Filters are labelled controls; the result count is announced when it changes.")
    s.callout(3, 216, 158, "Real table semantics: column headers, sortable by keyboard, current sort announced.")
    s.callout(4, 508, 158, "Status is text plus a distinct shape; freshness and truth label are columns, not tooltips.")
    s.callout(5, 216, 650, "Live updates go to a polite live region and can be paused; focus is never moved by an update.")
    s.callout(6, 1046, 48, "Selecting a row updates the details panel and, when switched back, the map selection.")
    return s


# --------------------------------------------------------------------------------------------- map, projector
def map_projector() -> Svg:
    s = Svg("map-projector", 1920, 1080, "Live operations map - projector (1920x1080)",
            "Wall-display variant: larger type, no side navigation, headline indicators, the map and a critical incident list. Read-only status; controls stay on the laptop.", "supervisor", ("map",))
    header(s, 1920, 64, scale=1.35)
    with s.group("region-kpi-strip", "key indicators"):
        for i, (lab, val, kind) in enumerate([("Open incidents", "5", "warn"), ("Critical", "2", "bad"), ("Emergency units en route", "1", "info"), ("Network mean speed", "34 km/h", "ok"), ("Data freshness", "fresh, 3 s", "ok")]):
            x = 24 + i * 372
            s.rect(x, 80, 356, 96, CARD, BORDER, 10)
            s.text(x + 16, 112, lab, 20, FG_MUTED)
            s.text(x + 16, 156, val, 34, FG, 700, mono=True)
            s.status(x + 318, 112, "", kind, 20)
    schematic(s, 24, 196, 1240, 800)
    with s.group("region-critical-queue", "critical incident list"):
        s.rect(1284, 196, 612, 800, CARD, BORDER, 10)
        s.text(1308, 240, "Critical and high incidents", 26, FG, 700)
        for i, (t, st, kind) in enumerate([("Stalled vehicle, B2-B3", "Critical - open, unacknowledged 04:12", "bad"), ("Congestion spillback, A3-A4", "High - acknowledged", "warn"), ("Pedestrian conflict, C2", "High - investigating", "warn")]):
            yy = 264 + i * 168
            s.rect(1300, yy, 580, 152, MUTED, BORDER, 10)
            s.status(1320, yy + 40, "Critical" if kind == "bad" else "High", kind, 22)
            s.text(1320, yy + 84, t, 24, FG, 700)
            s.text(1320, yy + 122, st, 20, FG_MUTED)
    with s.group("region-footer-status", "status footer"):
        s.rect(24, 1010, 1872, 56, CARD, BORDER, 8)
        s.text(44, 1046, "Simulated data - live feed connected - last update 09:14:52 UTC - projector view is read-only", 22, FG_MUTED)
    s.callout(1, 24, 80, "Headline indicators, each with a label and a number; not decorative gauges.")
    s.callout(2, 24, 196, "Larger map with 20 px minimum labels in the built UI; same line styles and text labels as the laptop view.")
    s.callout(3, 1284, 196, "Critical and high incidents only: readable at distance, status as text plus shape, no colour-only cues.")
    s.callout(4, 24, 1010, "Persistent 'simulated' and live-feed state; the display never presents stale data as live.")
    return s


# --------------------------------------------------------------------------------------------- field, mobile
def field_mobile() -> Svg:
    s = Svg("field-mobile", 390, 844, "Field view - mobile (390x844)",
            "Read-only responder view: assigned call, status, route summary with ETA, route steps as a list, and what changed. No control panels.", "field_responder", ("field",))
    with s.group("region-header", "banner"):
        s.rect(0, 0, 390, 56, CARD, BORDER, 0)
        s.text(16, 34, "Field view", 18, FG, 700)
        s.status(120, 34, "Live 4 s", "ok", 14)
        s.rect(288, 14, 88, 28, MUTED, INFO, 14)
        s.text(332, 33, "SIMULATED", 11, INFO, 700, "middle")
    with s.group("region-assignment", "assignment"):
        s.rect(12, 68, 366, 176, CARD, BORDER, 10)
        s.text(28, 100, "Structure fire - critical", 17, FG, 700)
        s.text(28, 124, "Unit engine-1 - fire-dispatch", 14, FG_MUTED)
        s.text(28, 150, "Status", 13, FG_MUTED)
        for i, (lab, done) in enumerate([("Assigned", True), ("Acknowledged", True), ("En route", True), ("On scene", False)]):
            x = 28 + i * 86
            s.circle(x + 6, 174, 8, ACCENT if done else "none", ACCENT if done else FG_MUTED, 2)
            s.text(x + 6, 204, lab, 11, FG if done else FG_MUTED, 400, "middle")
        s.text(28, 232, "Updated 09:14:52 UTC - fresh", 12, FG_MUTED)
    with s.group("region-route", "route summary"):
        s.rect(12, 256, 366, 132, CARD, BORDER, 10)
        s.text(28, 288, "Route and ETA", 15, FG, 700)
        s.text(28, 326, "1:34", 34, FG, 700, mono=True)
        s.text(112, 326, "+/- 0:14  predicted", 15, FG_MUTED)
        s.text(28, 352, "1,245 m via corridor C - 3 signals - avoids closed B2-B3", 12, FG_MUTED)
        s.text(28, 374, "Recalculated 09:14:40 - alternative route available", 12, FG_MUTED)
    with s.group("region-route-steps", "route steps list (text alternative to the map)"):
        s.rect(12, 400, 366, 232, CARD, BORDER, 10)
        s.text(28, 432, "Route steps", 15, FG, 700)
        for i, (st, d) in enumerate([("Depart int-c1 east on Corridor C", "300 m"), ("Continue through int-c2, int-c3", "600 m"), ("Turn onto cross street at int-c4", "300 m"), ("Arrive int-b4", "45 m")]):
            yy = 466 + i * 42
            s.text(28, yy, f"{i + 1}. {st}", 13, FG)
            s.text(362, yy, d, 13, FG_MUTED, 400, "end", mono=True)
    with s.group("region-changes", "what changed"):
        s.rect(12, 644, 366, 100, CARD, BORDER, 10)
        s.text(28, 676, "What changed", 15, FG, 700)
        s.status(28, 702, "Route changed 09:14:40 (segment closed)", "warn", 12)
        s.text(28, 726, "Pre-emption executed at int-c2, int-c3", 12, FG_MUTED)
    with s.group("region-bottom-nav", "navigation"):
        s.rect(0, 764, 390, 80, CARD, BORDER, 0)
        for i, lab in enumerate(["Assignment", "Incidents", "Handover"]):
            x = 12 + i * 122
            s.rect(x, 776, 116, 56, MUTED if i == 0 else "none", ACCENT if i == 0 else BORDER, 10, 2)
            s.text(x + 58, 810, lab, 14, FG, 600 if i == 0 else 400, "middle")
    s.callout(1, 12, 4, "Live state in text and shape with the age of the data; if the connection drops it reads 'Offline - last update 09:14:52' and never looks live.")
    s.callout(2, 12, 68, "Status stepper: every step is a labelled node, not a colour band.")
    s.callout(3, 12, 256, "ETA with uncertainty and a 'predicted' truth label.")
    s.callout(4, 12, 400, "Route steps are the text equivalent of the map; the map is a preview, not required to act.")
    s.callout(5, 12, 644, "What changed is explicit: route, closures, pre-emption.")
    s.callout(6, 12, 764, "Bottom navigation, three items, 56 px touch targets. No approve, request or execute controls exist on this view.")
    return s


# --------------------------------------------------------------------------------------------- incident, laptop
def incident_laptop() -> Svg:
    s = Svg("incident-laptop", 1366, 768, "Incident investigation - laptop (1366x768)",
            "Incident header with severity, status and owner; evidence timeline; ranked hypotheses labelled inferred; linked recommendations and commands; an action bar with a required note for resolve.", "operator", ("incident-detail",))
    header(s, 1366)
    nav(s, 0, 48, 200, 720, "Incidents")
    with s.group("region-incident-header", "incident header"):
        s.rect(216, 60, 1134, 96, CARD, BORDER, 8)
        s.text(232, 92, "Stalled vehicle - segment int-b2_int-b3", 20, FG, 700)
        s.status(232, 120, "Critical", "bad")
        s.text(340, 120, "Confidence 0.63 - inferred", 13, FG_MUTED)
        s.text(232, 144, "Opened 09:12:07 UTC - owner OPS - status Open", 13, FG_MUTED)
        for i, (lab, cur) in enumerate([("Open", True), ("Acknowledged", False), ("Investigating", False), ("Resolved", False)]):
            x = 760 + i * 146
            s.rect(x, 76, 138, 32, MUTED if cur else "none", ACCENT if cur else BORDER, 16, 2)
            s.text(x + 69, 97, lab, 13, FG, 600 if cur else 400, "middle")
        s.text(760, 134, "Allowed next steps only; an illegal transition is not offered.", 12, FG_MUTED)
    with s.group("region-evidence", "evidence timeline"):
        s.rect(216, 168, 380, 508, CARD, BORDER, 8)
        s.text(232, 200, "Evidence timeline", 16, FG, 700)
        for i, (t, src, mod) in enumerate([("09:14:31", "Edge model - blockage", "camera"), ("09:13:50", "Congestion detector", "loop"), ("09:13:02", "Loop det-b2-04 occupancy", "loop"), ("09:12:07", "Stall candidate raised", "fused")]):
            yy = 236 + i * 96
            s.line(238, yy - 8, 238, yy + 76, BORDER, 2)
            s.circle(238, yy, 7, ACCENT, FG, 2)
            s.text(256, yy + 4, f"{t} UTC - {src}", 13, FG, 600)
            s.text(256, yy + 26, f"modality: {mod} - truth label: inferred", 12, FG_MUTED)
            s.text(256, yy + 48, "Open evidence record", 12, INFO)
    with s.group("region-hypotheses", "hypotheses"):
        s.rect(608, 168, 400, 240, CARD, BORDER, 8)
        s.text(624, 200, "Hypotheses (not verified causes)", 16, FG, 700)
        for i, (h, p) in enumerate([("Stalled vehicle blocking lane", "0.63"), ("Signal fault upstream", "0.18"), ("Demand surge", "0.11")]):
            yy = 232 + i * 56
            s.text(624, yy, f"{i + 1}. {h}", 14, FG, 600)
            s.text(992, yy, p, 14, FG_MUTED, 400, "end", mono=True)
            s.text(624, yy + 20, "inferred - ranked by evidence, not confirmed", 12, FG_MUTED)
    with s.group("region-mini-map", "affected network"):
        s.rect(608, 420, 400, 256, CARD, BORDER, 8)
        s.text(624, 452, "Affected elements", 16, FG, 700)
        schematic(s, 620, 462, 376, 202, detail=False)
    with s.group("region-linked-actions", "linked recommendations and commands"):
        s.rect(1020, 168, 330, 508, CARD, BORDER, 8)
        s.text(1036, 200, "Recommendations and commands", 16, FG, 700)
        s.rect(1032, 218, 306, 130, MUTED, BORDER, 8)
        s.text(1044, 244, "Recommendation: divert via corridor C", 13, FG, 600)
        s.status(1044, 270, "Proposed - expires 09:24", "info", 12)
        s.text(1044, 294, "Benefit -74 s delay   Harm +12 s cross street", 12, FG_MUTED)
        s.button(1044, 308, 130, 30, "Request", primary=True, size=12)
        s.rect(1032, 360, 306, 96, MUTED, BORDER, 8)
        s.text(1044, 386, "Command: variable message sign", 13, FG, 600)
        s.status(1044, 412, "Requested - waiting for approval", "warn", 12)
        s.text(1044, 436, "Requested by operator, needs a supervisor", 12, FG_MUTED)
    with s.group("region-action-bar", "incident actions"):
        s.rect(216, 688, 1134, 68, CARD, BORDER, 8)
        s.button(232, 704, 150, 36, "Acknowledge", primary=True, size=13)
        s.button(394, 704, 150, 36, "Assign owner v", size=13)
        s.button(556, 704, 150, 36, "Escalate", size=13)
        s.button(718, 704, 150, 36, "Resolve...", size=13)
        s.rect(884, 704, 452, 36, "none", FG_MUTED, 6, 1)
        s.text(896, 727, "Add a note (required to resolve)...", 13, FG_MUTED)
    s.callout(1, 216, 60, "Header: severity as text plus shape, confidence with its truth label 'inferred', owner and status.")
    s.callout(2, 760, 76, "Status stepper shows only legal next steps; a transition conflict shows what changed instead of overwriting.")
    s.callout(3, 216, 168, "Evidence timeline: source, time, modality, truth label; each item opens the real record.")
    s.callout(4, 608, 168, "Hypotheses are ranked and labelled 'inferred'; nothing here is called the cause.")
    s.callout(5, 1020, 168, "Linked recommendations and commands with their own distinct state chips.")
    s.callout(6, 216, 688, "Action bar appears only for roles holding incidents.manage; resolve needs a note. Position is stable and never shifts on update.")
    return s


# --------------------------------------------------------------------------------------------- emergency dispatch, laptop
def emergency_laptop() -> Svg:
    s = Svg("emergency-laptop", 1366, 768, "Emergency dispatch detail - laptop (1366x768)",
            "Call summary, route alternatives with ETA and uncertainty and a mini map, unit assignment with status controls, the pre-emption command's lifecycle, and a shared timeline.", "dispatcher", ("dispatch-detail",))
    header(s, 1366)
    nav(s, 0, 48, 200, 720, "Dispatch")
    with s.group("region-call", "call summary"):
        s.rect(216, 60, 1134, 84, CARD, BORDER, 8)
        s.text(232, 92, "Structure fire - call 3f9a...", 20, FG, 700)
        s.status(232, 120, "Critical", "bad")
        s.text(340, 120, "Status: Unit assigned - reported 09:10:21 UTC - source reliability: verified dispatch - simulated", 13, FG_MUTED)
    with s.group("region-routes", "route alternatives"):
        s.rect(216, 156, 640, 330, CARD, BORDER, 8)
        s.text(232, 188, "Route alternatives for engine-1", 16, FG, 700)
        for i, (name, eta, unc, dist, sel) in enumerate([("Route 1 (selected)", "1:34", "+/- 0:14", "1,245 m", True), ("Route 2", "1:58", "+/- 0:19", "1,510 m", False), ("Route 3", "2:21", "+/- 0:25", "1,802 m", False)]):
            yy = 204 + i * 92
            s.rect(228, yy, 616, 82, MUTED if sel else "none", ACCENT if sel else BORDER, 8, 2)
            s.text(244, yy + 26, name, 14, FG, 700)
            s.text(244, yy + 50, f"ETA {eta} {unc} - predicted - {dist}", 13, FG, 400, mono=True)
            s.text(244, yy + 70, "avoids closed B2-B3" if sel else "through hazard: congestion A3", 12, FG_MUTED)
            s.button(716, yy + 24, 116, 34, "Select" if not sel else "Selected", primary=sel, size=13, disabled=sel)
    with s.group("region-mini-map", "route on map"):
        s.rect(216, 498, 640, 258, CARD, BORDER, 8)
        schematic(s, 228, 510, 616, 234, detail=False)
    with s.group("region-unit", "unit assignment"):
        s.rect(868, 156, 482, 200, CARD, BORDER, 8)
        s.text(884, 188, "Unit engine-1", 16, FG, 700)
        s.text(884, 212, "fire-dispatch - capability: suppression", 13, FG_MUTED)
        for i, (lab, done) in enumerate([("Assigned", True), ("Acknowledged", True), ("En route", False), ("On scene", False)]):
            x = 884 + i * 112
            s.circle(x + 6, 244, 8, ACCENT if done else "none", ACCENT if done else FG_MUTED, 2)
            s.text(x + 6, 272, lab, 12, FG if done else FG_MUTED, 400, "middle")
        s.button(884, 296, 200, 40, "Record: en route", primary=True, size=13)
        s.button(1096, 296, 240, 40, "Mark unavailable...", size=13)
    with s.group("region-preemption", "pre-emption command"):
        s.rect(868, 368, 482, 200, CARD, BORDER, 8)
        s.text(884, 400, "Signal pre-emption (SC-2)", 16, FG, 700)
        s.text(884, 424, "Needs a second person: supervisor or incident commander", 13, FG_MUTED)
        for i, (lab, cur, done) in enumerate([("Requested", False, True), ("Approved", True, False), ("Executing", False, False), ("Executed", False, False)]):
            x = 884 + i * 112
            s.rect(x, 440, 104, 30, MUTED if cur else "none", ACCENT if cur or done else BORDER, 15, 2)
            s.text(x + 52, 460, lab, 12, FG, 600 if cur else 400, "middle")
        s.status(884, 500, "Approved by incident_commander eve", "ok", 13)
        s.text(884, 524, "Executed by the command executor, never by a person", 12, FG_MUTED)
        s.button(884, 536, 236, 28, "Open command detail", size=13)
    with s.group("region-timeline", "shared timeline"):
        s.rect(868, 580, 482, 176, CARD, BORDER, 8)
        s.text(884, 610, "Timeline (dispatch and traffic operations)", 15, FG, 700)
        for i, t in enumerate(["09:14:40  Route recalculated: segment closed", "09:12:30  Pre-emption requested by dispatcher dana", "09:11:02  Unit engine-1 acknowledged", "09:10:21  Call received, dispatched"]):
            s.text(884, 636 + i * 26, t, 12, FG_MUTED if i else FG)
    s.callout(1, 216, 60, "Call summary with priority as text plus shape, status, source reliability and truth label.")
    s.callout(2, 216, 156, "Route alternatives: ETA with uncertainty, distance, constraints (through hazard / avoids closure). Selecting is explicit; the selected one is clearly marked.")
    s.callout(3, 216, 498, "Mini map mirrors the selected route; the route list and steps are the accessible equivalent.")
    s.callout(4, 868, 156, "Unit assignment: only legal next status buttons are offered (dispatch role).")
    s.callout(5, 868, 368, "Pre-emption lifecycle as labelled stages; who approved is shown, and that a person never executes.")
    s.callout(6, 868, 580, "One shared timeline for dispatch and traffic operations; append-only.")
    return s


# --------------------------------------------------------------------------------------------- approval confirmation
def approval_dialog() -> Svg:
    s = Svg("approval-confirm-laptop", 1366, 768, "Command approval confirmation - laptop (1366x768)",
            "Modal confirmation for approving a command: target, safety class, reason, expected effect and harm, constraints, expiry, four-eyes note, policy status, and Cancel/Deny/Approve with Cancel as the default focus.", "supervisor", ("actions-command-detail",))
    header(s, 1366)
    nav(s, 0, 48, 200, 720, "Actions")
    with s.group("region-background", "command detail (inert behind the dialog)"):
        s.rect(216, 60, 1134, 696, CARD, BORDER, 8)
        s.text(232, 96, "Command 9c1f... - diversion", 20, FG, 700)
        s.status(232, 124, "Requested - waiting for approval", "warn")
    s.raw('<rect x="0" y="48" width="1366" height="720" fill="#000000" fill-opacity="0.6"/>')
    with s.group("region-dialog", "dialog"):
        s.rect(383, 84, 600, 640, CARD, FG, 14, 2)
        s.text(407, 124, "Approve this command?", 22, FG, 700)
        s.text(407, 148, "Review before you decide. Approval passes the command to policy; it does not execute it.", 13, FG_MUTED)
        for i, (k, v) in enumerate([("Target", "diversion_adapter -> int-b2_int-b3"), ("Action", "Diversion (SC-1, traffic-influencing)"), ("Requested by", "operator alex (role operator)"),
                                    ("Reason", "Stalled vehicle blocks both lanes"), ("Expected benefit", "-74 s corridor delay (predicted)"), ("Expected harm", "+12 s cross-street delay (predicted)"),
                                    ("Constraints", "min pedestrian clearance 7 s - within bounds"), ("Expires", "09:24:00 UTC (in 8:14)")]):
            yy = 186 + i * 34
            s.text(407, yy, k, 13, FG_MUTED)
            s.text(560, yy, v, 14, FG, 600)
        s.line(407, 466, 959, 466, BORDER, 1)
        s.status(407, 494, "You are a different person from the requester - four-eyes satisfied", "ok", 13)
        s.status(407, 522, "Policy is checked at submit: role, evidence freshness, target validity", "info", 13)
        s.text(407, 556, "Decision reason (required to deny)", 13, FG_MUTED)
        s.rect(407, 566, 552, 64, "none", FG_MUTED, 6, 1)
        s.button(407, 660, 130, 44, "Cancel", size=15)
        s.button(669, 660, 130, 44, "Deny...", size=15)
        s.button(811, 660, 148, 44, "Approve", primary=True, size=15)
    s.callout(1, 383, 84, "Modal dialog: focus moves in and is trapped; Escape cancels; focus returns to the trigger. Background is inert.")
    s.callout(2, 383, 170, "Everything the approver needs to decide: target, class, reason, expected benefit AND harm, constraints, expiry (UX-03).")
    s.callout(3, 383, 480, "Four-eyes is stated, not just enforced; if the user were the requester this row reads 'You requested this - you cannot approve it' and Approve is absent.")
    s.callout(4, 383, 660, "Cancel is the default-focused button; Approve is disabled while the request is in flight (no double submit) and the result is shown, never silent.")
    return s


DOC = OUT.parent / "WIREFRAMES.md"
BEGIN, END = "<!-- BEGIN GENERATED: wireframes -->", "<!-- END GENERATED: wireframes -->"


def render_doc_block(all_svgs: list[Svg]) -> str:
    out = ["| Wireframe | Layout | Role shown | Screen(s) |", "|---|---|---|---|"]
    for sv in all_svgs:
        out.append(f"| [`{sv.name}.svg`](wireframes/{sv.name}.svg) | {sv.w}x{sv.h} | `{sv.role}` | {', '.join('`' + x + '`' for x in sv.screens)} |")
    for sv in all_svgs:
        out += ["", f"#### `{sv.name}` - {sv.title}", "", sv.desc, "", "| # | What it says |", "|---|---|"]
        out += [f"| {n} | {meaning} |" for n, meaning in sorted(sv.callouts)]
    return "\n".join(out) + "\n"


def main() -> None:
    builders = [map_laptop, list_laptop, map_projector, field_mobile, incident_laptop, emergency_laptop, approval_dialog]
    pages, built = [], []
    for build in builders:
        svg = build()
        path = svg.save()
        pages.append((svg.name, svg.title))
        built.append(svg)
        print(f"wrote {path.name} ({len(svg.callouts)} callouts, role {svg.role})")
    write_index(pages)
    if DOC.is_file():
        text = DOC.read_text(encoding="utf-8")
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        DOC.write_text(f"{head}{BEGIN}\n\n{render_doc_block(built)}\n{END}{tail}", encoding="utf-8")
        print(f"updated generated tables in {DOC.name}")


if __name__ == "__main__":
    main()
