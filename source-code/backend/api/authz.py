"""P08.04: who may call what, derived from the one UX inventory.

`source-code/frontend/src/config/inventory.json` (P08.01) says which capability
each role holds and which capability each endpoint needs. This module reads it -
the API does not carry a second copy of the matrix - and answers two questions:
what capabilities does a set of roles add up to, and what does a given
(method, route template) require. An endpoint the inventory does not list is
denied by default (`tools/inventory.py` fails the build if the app serves an
endpoint the inventory lacks, so in practice this only triggers if someone adds
a route and skips the inventory).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

INVENTORY_PATH = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "config" / "inventory.json"
)


@dataclass(frozen=True)
class EndpointPolicy:
    capability: str | None
    public: bool


@lru_cache(maxsize=1)
def inventory() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def capabilities_of(roles: tuple[str, ...] | list[str]) -> frozenset[str]:
    held = set(roles)
    return frozenset(
        cap for cap, spec in inventory()["capabilities"].items() if held & set(spec["roles"])
    )


@lru_cache(maxsize=1)
def _endpoint_table() -> dict[tuple[str, str, str], EndpointPolicy]:
    return {
        (a.get("service", "api"), a["method"], a["path"]): EndpointPolicy(
            a["capability"], bool(a.get("public"))
        )
        for a in inventory()["apis"]
    }


def policy_for(method: str, route_path: str, service: str = "api") -> EndpointPolicy | None:
    """`service` is the process that serves the endpoint: the operator API (`api`) or the separate scenario-control API."""
    return _endpoint_table().get((service, method.upper(), route_path))
