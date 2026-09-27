#!/usr/bin/env python3
"""P13.03: the generated API reference and OpenAPI export are current with the real endpoint inventory and the running app's own schema.

    python source-code/scripts/verify_api_docs.py

Writes `docs/evidence/p13_03_api_docs.json`.
"""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SOURCE_ROOT.parent
sys.path.insert(0, str(SOURCE_ROOT))

from backend.evidence import Evidence  # noqa: E402
from scripts.build_api_docs import API_DOCS, build_openapi, load_inventory, render  # noqa: E402


def main() -> int:
    ev = Evidence("P13.03", "p13_03_api_docs", docs_name="p13_03_api_docs")
    inv = load_inventory()
    openapi = build_openapi()
    wanted_md = render(inv, openapi)

    md_path = API_DOCS / "API_REFERENCE.md"
    current_md = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    ev.check(
        "the_api_reference_matches_a_fresh_render_of_the_real_inventory_and_schema",
        current_md == wanted_md,
        "run source-code/scripts/build_api_docs.py" if current_md != wanted_md else "current",
    )

    openapi_path = API_DOCS / "openapi.json"
    ev.check(
        "the_openapi_export_exists",
        openapi_path.is_file(),
        str(openapi_path),
    )

    no_capability = [
        a["path"]
        for a in inv["apis"]
        if a.get("capability") is None and not a.get("public") and a["path"] != "/api/v1/me"
    ]
    ev.check(
        "every_endpoint_other_than_health_and_me_declares_a_capability_or_is_public",
        not no_capability,
        str(no_capability),
    )

    real_paths = {p.rstrip("/") for p in openapi["paths"]} | {"/api/v1/live"}
    inventory_api_paths = {a["path"] for a in inv["apis"] if a.get("service", "api") == "api"}
    missing_from_app = inventory_api_paths - real_paths
    ev.check(
        "every_operator_api_path_the_inventory_names_is_a_path_the_app_really_serves",
        not missing_from_app,
        str(missing_from_app),
    )

    ev.metrics = {
        "endpoints": len(inv["apis"]),
        "capabilities": len(inv["capabilities"]),
        "openapi_paths": len(openapi["paths"]),
    }
    return ev.finish()


if __name__ == "__main__":
    raise SystemExit(main())
