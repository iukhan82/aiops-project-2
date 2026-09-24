"""P08.01: validate `src/config/inventory.json` and (re)generate the tables in
docs/ux/ROLE_JOURNEYS_AND_SCREEN_INVENTORY.md from it.

    python source-code/frontend/tools/inventory.py            # validate, write evidence
    python source-code/frontend/tools/inventory.py --render   # rewrite the generated tables in the doc
    python source-code/frontend/tools/inventory.py --check    # fail if the doc's tables are stale

The inventory is the one place that says who may see and do what. This tool
proves it is consistent with the two things it must agree with: the binding
role model (`backend/roles.py`, which mirrors docs/security/
ROLES_AND_ACTION_AUTHORITY.md) and the FastAPI applications that actually
serve the endpoints it lists. An endpoint marked `implemented` must exist; an
endpoint the app serves must be listed; an endpoint marked `planned:<task>`
is counted so a later task cannot quietly leave one unbuilt (P08.10 requires
zero).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402
from backend.roles import HUMAN_ROLES  # noqa: E402

INVENTORY = SOURCE_ROOT / "frontend" / "src" / "config" / "inventory.json"
DOC = REPO_ROOT / "docs" / "ux" / "ROLE_JOURNEYS_AND_SCREEN_INVENTORY.md"
BEGIN, END = "<!-- BEGIN GENERATED: inventory -->", "<!-- END GENERATED: inventory -->"
NOT_IN_INVENTORY = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def load() -> dict:
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def served_operations() -> dict[str, set[tuple[str, str]]]:
    """(method, path) actually served by each FastAPI app, WebSocket routes included."""
    from backend.api.app import app as api_app  # noqa: PLC0415
    from backend.scenario_control.app import app as demo_app  # noqa: PLC0415

    out: dict[str, set[tuple[str, str]]] = {}
    for name, app in (("api", api_app), ("scenario-control", demo_app)):
        ops: set[tuple[str, str]] = set()
        for route in app.routes:
            path = getattr(route, "path", None)
            if path is None or path in NOT_IN_INVENTORY:
                continue
            methods = getattr(route, "methods", None)
            if methods:
                ops |= {(m, path) for m in methods if m not in ("HEAD", "OPTIONS")}
            else:
                ops.add(("WEBSOCKET", path))
        out[name] = ops
    return out


def problems(inv: dict) -> list[str]:
    errs: list[str] = []
    roles, caps = inv["roles"], inv["capabilities"]
    screens = {s["id"]: s for s in inv["screens"]}
    apis = {a["id"]: a for a in inv["apis"]}
    if set(roles) != set(HUMAN_ROLES):
        errs.append(f"roles differ from backend/roles.py: {set(roles) ^ set(HUMAN_ROLES)}")
    for cap, spec in caps.items():
        bad = set(spec["roles"]) - set(roles)
        if bad:
            errs.append(f"capability {cap} names unknown roles {bad}")
    for role, spec in roles.items():
        home = screens.get(spec["home"])
        if home is None:
            errs.append(f"{role}: home screen {spec['home']!r} does not exist")
        elif home["capability"] and role not in caps[home["capability"]]["roles"]:
            errs.append(
                f"{role}: home screen {spec['home']!r} needs {home['capability']}, which the role does not hold"
            )
    for s in screens.values():
        if s["capability"] is not None and s["capability"] not in caps:
            errs.append(f"screen {s['id']}: unknown capability {s['capability']}")
        for api_id in s["api"]:
            if api_id not in apis:
                errs.append(f"screen {s['id']}: unknown api {api_id}")
            elif (
                apis[api_id]["capability"]
                and s["capability"]
                and apis[api_id]["capability"] != s["capability"]
                and not (
                    set(caps[s["capability"]]["roles"])
                    <= set(caps[apis[api_id]["capability"]]["roles"])
                )
            ):
                errs.append(
                    f"screen {s['id']} ({s['capability']}) requires {api_id} ({apis[api_id]['capability']}) but some roles that may open the screen may not call it - make it optional_api"
                )
        for api_id in s["optional_api"]:
            if api_id not in apis:
                errs.append(f"screen {s['id']}: unknown optional api {api_id}")
        if s["capability"] is not None and s["api"]:
            for needed in ("loading", "error", "forbidden"):
                if needed not in s["states"]:
                    errs.append(f"screen {s['id']}: state {needed!r} is not designed")
        if s["live"] and not {"stale"} <= set(s["states"]):
            errs.append(f"screen {s['id']}: a live screen must design the stale state")
        if (
            s["live"]
            and s["capability"]
            and "reconnecting" not in s["states"]
            and s["id"]
            not in (
                "incident-detail",
                "dispatch-detail",
                "actions-command-detail",
                "actions-recommendations",
                "operations",
            )
        ):
            errs.append(f"screen {s['id']}: a live list screen must design the reconnecting state")
        if s["capability"] is not None and not caps[s["capability"]]["roles"]:
            errs.append(f"screen {s['id']}: no role can open it")
    for api in apis.values():
        if api["capability"] is not None and api["capability"] not in caps:
            errs.append(f"api {api['id']}: unknown capability {api['capability']}")
        if not api["status"].startswith(("implemented", "planned:")):
            errs.append(f"api {api['id']}: status {api['status']!r}")
    used_apis = {a for s in screens.values() for a in (*s["api"], *s["optional_api"])}
    for api_id, api in apis.items():
        if api_id not in used_apis and not api.get("public") and api_id not in ("me",):
            errs.append(f"api {api_id} is not used by any screen")
    journeys = {j["role"]: j for j in inv["journeys"]}
    for role in roles:
        if role not in journeys:
            errs.append(f"{role}: no journey")
            continue
        for step in journeys[role]["steps"]:
            if step["screen"] not in screens:
                errs.append(f"{role} journey: unknown screen {step['screen']}")
            cap = step.get("capability") or screens.get(step["screen"], {}).get("capability")
            if cap and role not in caps[cap]["roles"]:
                errs.append(
                    f"{role} journey: step on {step['screen']} needs {cap}, which the role does not hold"
                )
        if not journeys[role]["failure_cases"]:
            errs.append(f"{role} journey: no failure cases")
    return errs


def api_problems(inv: dict) -> tuple[list[str], dict]:
    served = served_operations()
    errs: list[str] = []
    listed: dict[str, set[tuple[str, str]]] = {"api": set(), "scenario-control": set()}
    counts = {"implemented": 0, "planned": 0}
    for api in inv["apis"]:
        service = api.get("service", "api")
        key = (api["method"], api["path"])
        if api["status"] == "implemented":
            counts["implemented"] += 1
            if key not in served[service]:
                errs.append(f"{api['id']}: marked implemented but {key} is not served by {service}")
            listed[service].add(key)
        else:
            counts["planned"] += 1
            if key in served[service]:
                errs.append(
                    f"{api['id']}: marked {api['status']} but it is already served - mark it implemented"
                )
            listed[service].add(key)
    for service, ops in served.items():
        for op in sorted(ops - listed[service]):
            errs.append(f"{service} serves {op} which the inventory does not list")
    return errs, counts


def render(inv: dict) -> str:
    roles, caps = inv["roles"], inv["capabilities"]
    out = [
        "### Roles and capabilities",
        "",
        "| Capability | " + " | ".join(roles) + " |",
        "|---|" + "---|" * len(roles),
    ]
    for cap, spec in caps.items():
        out.append(
            f"| `{cap}` | " + " | ".join("yes" if r in spec["roles"] else "-" for r in roles) + " |"
        )
    out += [
        "",
        "Home screen per role: "
        + "; ".join(f"`{r}` -> `{s['home']}`" for r, s in roles.items())
        + ".",
        "",
        "### Screens",
        "",
        "| Screen | Path | Capability | Live | Designed states | Optional by capability | Task |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in inv["screens"]:
        out.append(
            f"| {s['title']} (`{s['id']}`) | `{s['path']}` | {('`' + s['capability'] + '`') if s['capability'] else 'public'} | {'yes' if s['live'] else 'no'} | "
            f"{', '.join(s['states'])} | {', '.join('`' + a + '`' for a in s['optional_api']) or '-'} | {s['task']} |"
        )
    out += [
        "",
        "### API endpoints",
        "",
        "| Id | Method and path | Capability | Status |",
        "|---|---|---|---|",
    ]
    for a in inv["apis"]:
        out.append(
            f"| `{a['id']}` | `{a['method']} {a['path']}` | {('`' + a['capability'] + '`') if a['capability'] else 'public' if a.get('public') else 'any signed-in role'} | {a['status']} |"
        )
    out += ["", "### Journeys", ""]
    for j in inv["journeys"]:
        out += [f"**`{j['role']}`** - {j['goal']}", ""]
        out += [f"{i}. `{st['screen']}`: {st['action']}" for i, st in enumerate(j["steps"], 1)]
        out += ["", "Failure cases designed for: " + "; ".join(j["failure_cases"]) + ".", ""]
    return "\n".join(out).rstrip() + "\n"


def splice(doc: str, generated: str) -> str:
    head, rest = doc.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n\n{generated}\n{END}{tail}"


def main() -> int:
    inv = load()
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    generated = render(inv)
    if mode == "--render":
        DOC.write_text(splice(DOC.read_text(encoding="utf-8"), generated), encoding="utf-8")
        print(f"rewrote generated tables in {DOC}")
        return 0
    ev = Evidence("P08.01")
    structural = problems(inv)
    ev.check(
        "inventory_is_internally_consistent_roles_capabilities_screens_apis_journeys",
        not structural,
        detail="; ".join(structural[:3]),
    )
    api_errs, counts = api_problems(inv)
    ev.check(
        "inventory_matches_what_the_fastapi_apps_actually_serve",
        not api_errs,
        detail="; ".join(api_errs[:3]),
    )
    ev.check("roles_equal_the_binding_p02_08_role_set", set(inv["roles"]) == set(HUMAN_ROLES))
    ev.check(
        "every_role_has_a_home_screen_and_a_journey_with_failure_cases",
        all(any(j["role"] == r for j in inv["journeys"]) for r in inv["roles"]),
    )
    ev.check(
        "every_live_screen_designs_stale_state_and_every_data_screen_designs_loading_error_forbidden",
        not [e for e in structural if "state" in e],
        detail="; ".join(e for e in structural if "state" in e),
    )
    doc_text = DOC.read_text(encoding="utf-8") if DOC.is_file() else ""
    fresh = BEGIN in doc_text and splice(doc_text, generated) == doc_text
    ev.check(
        "the_generated_tables_in_the_inventory_document_are_current",
        fresh,
        detail="run tools/inventory.py --render" if not fresh else "",
    )
    ev.metrics["counts"] = {
        "roles": len(inv["roles"]),
        "capabilities": len(inv["capabilities"]),
        "screens": len(inv["screens"]),
        "apis": len(inv["apis"]),
        "apis_implemented": counts["implemented"],
        "apis_planned": counts["planned"],
        "journeys": len(inv["journeys"]),
    }
    ev.metrics["planned_apis_by_task"] = {}
    for a in inv["apis"]:
        if a["status"].startswith("planned:"):
            ev.metrics["planned_apis_by_task"].setdefault(a["status"].split(":")[1], []).append(
                a["id"]
            )
    if mode == "--check":
        return 0 if fresh and not structural else 1
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
