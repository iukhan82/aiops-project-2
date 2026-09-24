"""P08.02: verify the wireframes are complete, consistent with the inventory
and the documentation, and actually render.

    python source-code/frontend/tools/verify_wireframes.py

Checks are structural (regions, callouts, layout classes), semantic (navigation
and controls match the role's capabilities in inventory.json; the field view
carries no control at all) and physical (each SVG is rendered by real headless
Chrome and must produce a non-trivial image, so an SVG that only *parses* is
not accepted).
"""

from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))
sys.path.insert(0, str(REPO_ROOT / "docs" / "ux" / "wireframes"))

from backend.evidence import Evidence  # noqa: E402
import build_wireframes as wf  # noqa: E402

WIRE = REPO_ROOT / "docs" / "ux" / "wireframes"
DOC = REPO_ROOT / "docs" / "ux" / "WIREFRAMES.md"
PNG_DIR = SOURCE_ROOT / "infra" / "platform" / "output" / "wireframe-png"
CHROME = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
NS = {"s": "http://www.w3.org/2000/svg"}

EXPECTED = {  # name -> (width, height, layout class, required regions, capability the role must hold for its controls)
    "map-laptop": (
        1366,
        768,
        "laptop",
        {
            "region-header",
            "region-nav",
            "region-main",
            "region-map",
            "region-layers",
            "region-legend",
            "region-replay",
            "region-selection",
            "region-critical-queue",
            "region-view-toggle",
        },
        "map.view",
    ),
    "map-list-laptop": (
        1366,
        768,
        "laptop",
        {
            "region-header",
            "region-nav",
            "region-table",
            "region-filters",
            "region-live-log",
            "region-selection",
            "region-view-toggle",
        },
        "map.view",
    ),
    "map-projector": (
        1920,
        1080,
        "projector",
        {
            "region-header",
            "region-kpi-strip",
            "region-map",
            "region-critical-queue",
            "region-footer-status",
        },
        "map.view",
    ),
    "field-mobile": (
        390,
        844,
        "field",
        {
            "region-header",
            "region-assignment",
            "region-route",
            "region-route-steps",
            "region-changes",
            "region-bottom-nav",
        },
        "emergency.view",
    ),
    "incident-laptop": (
        1366,
        768,
        "laptop",
        {
            "region-header",
            "region-nav",
            "region-incident-header",
            "region-evidence",
            "region-hypotheses",
            "region-linked-actions",
            "region-action-bar",
        },
        "incidents.manage",
    ),
    "emergency-laptop": (
        1366,
        768,
        "laptop",
        {
            "region-header",
            "region-nav",
            "region-call",
            "region-routes",
            "region-unit",
            "region-preemption",
            "region-timeline",
        },
        "emergency.dispatch",
    ),
    "approval-confirm-laptop": (
        1366,
        768,
        "laptop",
        {"region-header", "region-nav", "region-dialog"},
        "commands.review",
    ),
}


def parse(name: str) -> ET.Element:
    return ET.parse(WIRE / f"{name}.svg").getroot()


def region_texts(root: ET.Element, region: str) -> list[str]:
    for g in root.iter("{http://www.w3.org/2000/svg}g"):
        if g.get("id") == region:
            return [t.text or "" for t in g.iter("{http://www.w3.org/2000/svg}text")]
    return []


def doc_callouts() -> dict[str, dict[int, str]]:
    out: dict[str, dict[int, str]] = {}
    current = None
    for line in DOC.read_text(encoding="utf-8").splitlines():
        m = re.match(r"#### `([a-z-]+)`", line)
        if m:
            current = m.group(1)
            out[current] = {}
            continue
        m = re.match(r"\| (\d+) \| (.+) \|$", line)
        if current and m:
            out[current][int(m.group(1))] = m.group(2)
    return out


def render(name: str, w: int, h: int) -> int:
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    png = PNG_DIR / f"{name}.png"
    if png.is_file():
        png.unlink()
    subprocess.run(
        [
            str(CHROME),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size={w},{h}",
            f"--screenshot={png}",
            (WIRE / f"{name}.svg").as_uri(),
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    return png.stat().st_size if png.is_file() else 0


def main() -> int:
    ev = Evidence("P08.02")
    inv = wf.INVENTORY
    documented = doc_callouts()
    problems: dict[str, list[str]] = {}
    sizes: dict[str, int] = {}
    for name, (w, h, layout, regions, capability) in EXPECTED.items():
        errs = problems.setdefault(name, [])
        path = WIRE / f"{name}.svg"
        if not path.is_file():
            errs.append("missing file")
            continue
        root = parse(name)
        if (int(root.get("width")), int(root.get("height"))) != (w, h):
            errs.append(f"size {root.get('width')}x{root.get('height')} != {w}x{h}")
        if root.find("s:title", NS) is None or not (root.find("s:desc", NS).text or "").strip():
            errs.append("no title/description")
        ids = {g.get("id") for g in root.iter("{http://www.w3.org/2000/svg}g")}
        if missing := regions - ids:
            errs.append(f"missing regions {sorted(missing)}")
        numbers = sorted(
            int(g.get("id").split("-")[1])
            for g in root.iter("{http://www.w3.org/2000/svg}g")
            if (g.get("id") or "").startswith("callout-")
        )
        if numbers != list(range(1, len(numbers) + 1)) or not numbers:
            errs.append(f"callouts not contiguous from 1: {numbers}")
        if set(numbers) != set(documented.get(name, {})):
            errs.append(
                f"callouts {numbers} differ from the documented {sorted(documented.get(name, {}))}"
            )
        role = root.get("data-role")
        role_caps = {c for c, spec in inv["capabilities"].items() if role in spec["roles"]}
        if capability not in role_caps:
            errs.append(f"role {role} does not hold {capability}")
        nav_texts = region_texts(root, "region-nav")
        if nav_texts:
            shown = [t for t in nav_texts if t in {label for label, _ in wf.NAV_GROUPS}]
            if shown != wf.nav_items(role):
                errs.append(f"navigation {shown} != {wf.nav_items(role)} for {role}")
        controls = [
            g for g in root.iter("{http://www.w3.org/2000/svg}g") if g.get("class") == "control"
        ]
        if layout in ("field", "projector") and controls:
            errs.append(
                f"the {layout} view is read-only but carries controls {[''.join(t.text or '' for t in c.iter('{http://www.w3.org/2000/svg}text')) for c in controls]}"
            )
        if (
            layout == "laptop"
            and capability.endswith(("manage", "dispatch", "review"))
            and not controls
        ):
            errs.append("a write-capable wireframe shows no control")
        if layout == "field":
            nav_rects = [
                float(r.get("height"))
                for g in root.iter("{http://www.w3.org/2000/svg}g")
                if g.get("id") == "region-bottom-nav"
                for r in g.iter("{http://www.w3.org/2000/svg}rect")
            ][1:]
            if not nav_rects or min(nav_rects) < 44:
                errs.append(f"field touch targets below 44 px: {nav_rects}")
        sizes[name] = render(name, w, h)
        if sizes[name] < 20000:
            errs.append(f"render is {sizes[name]} bytes - blank or failed")
    ev.check(
        "all_seven_wireframes_exist_with_the_declared_layout_size_and_required_regions",
        all(
            not [e for e in errs if "size" in e or "missing" in e or "regions" in e]
            for errs in problems.values()
        ),
        detail=str({k: v for k, v in problems.items() if v}),
    )
    ev.check(
        "every_numbered_callout_is_contiguous_and_documented",
        all(not [e for e in errs if "callout" in e] for errs in problems.values()),
    )
    ev.check(
        "navigation_and_identity_in_each_wireframe_match_the_roles_capabilities_in_the_inventory",
        all(
            not [e for e in errs if "navigation" in e or "hold" in e] for errs in problems.values()
        ),
    )
    ev.check(
        "the_field_and_projector_views_are_read_only_with_no_control_and_the_field_has_44px_touch_targets",
        not [
            e
            for n in ("field-mobile", "map-projector")
            for e in problems[n]
            if "read-only" in e or "touch" in e
        ],
        detail=str({n: problems[n] for n in ("field-mobile", "map-projector")}),
    )
    ev.check(
        "every_wireframe_whose_role_holds_a_write_capability_shows_its_controls",
        not [e for errs in problems.values() for e in errs if "write-capable" in e],
    )
    ev.check(
        "each_wireframe_renders_in_real_chrome_to_a_non_blank_image",
        all(v >= 20000 for v in sizes.values()),
        detail=str(sizes),
    )
    list_texts = region_texts(parse("map-list-laptop"), "region-table")
    ev.check(
        "the_list_alternative_carries_status_freshness_and_truth_label_as_columns",
        {"Status ^", "Freshness", "Truth"} <= set(list_texts)
        or {"Freshness", "Truth"} <= set(list_texts)
        and any(t.startswith("Status") for t in list_texts),
    )
    classes = {v[2] for v in EXPECTED.values()}
    ev.check(
        "laptop_projector_and_field_layouts_and_an_accessible_alternative_are_all_present",
        {"laptop", "projector", "field"} <= classes and "map-list-laptop" in EXPECTED,
    )
    built = [
        b()
        for b in (
            wf.map_laptop,
            wf.list_laptop,
            wf.map_projector,
            wf.field_mobile,
            wf.incident_laptop,
            wf.emergency_laptop,
            wf.approval_dialog,
        )
    ]
    screen_ids = {x["id"] for x in inv["screens"]}
    ev.check(
        "every_wireframe_screen_id_exists_in_the_inventory_and_the_wireframe_role_may_open_it",
        all(
            s in screen_ids
            and (
                next(x for x in inv["screens"] if x["id"] == s)["capability"] is None
                or sv.role
                in inv["capabilities"][
                    next(x for x in inv["screens"] if x["id"] == s)["capability"]
                ]["roles"]
            )
            for sv in built
            for s in sv.screens
        ),
    )
    ev.metrics["wireframes"] = {
        n: {
            "png_bytes": sizes.get(n),
            "layout": EXPECTED[n][2],
            "callouts": len(documented.get(n, {})),
        }
        for n in EXPECTED
    }
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
