"""P03.09: one-off visual sanity check - plot the P03.03 device catalog on
the district network's local plane, colored by device_type. This is a
manual QA aid, not part of `build_and_verify.py` or the pytest suite: it
needs `matplotlib`, which is deliberately NOT added to
source-code/requirements-*.txt (no runtime/test code depends on it, the
same way P02.09's diagram rendering used an ad hoc `npx @mermaid-js/mermaid-cli`
invocation rather than a committed dependency). Install it yourself first:

    pip install matplotlib
    python source-code/simulator/verification/plot_device_map.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt

SIMULATOR_DIR = Path(__file__).resolve().parent.parent
NET_FILE = SIMULATOR_DIR / "network" / "output" / "district.net.xml"
NOD_FILE = SIMULATOR_DIR / "network" / "plain" / "district.nod.xml"
OUTPUT_PATH = Path(__file__).resolve().parent / "output" / "device_map.png"

sys.path.insert(0, str(SIMULATOR_DIR / "sensors"))
from build_sensor_catalog import build_catalog  # noqa: E402

sys.path.insert(0, str(SIMULATOR_DIR / "verification"))
from verify_bounds import inverse_project  # noqa: E402

COLORS = {
    "inductive_loop": "tab:blue",
    "cycle_counter": "tab:green",
    "crossing_detector": "tab:orange",
    "signal_controller": "tab:red",
    "weather_station": "tab:purple",
    "road_condition_sensor": "tab:brown",
}


def main() -> None:
    catalog = build_catalog(NET_FILE, NOD_FILE)
    devices = catalog["devices"]

    fig, ax = plt.subplots(figsize=(9, 8))
    for device_type, color in COLORS.items():
        xs, ys = [], []
        for device in devices:
            if device["device_type"] != device_type:
                continue
            x, y = inverse_project(device["location"]["latitude"], device["location"]["longitude"])
            xs.append(x)
            ys.append(y)
        ax.scatter(xs, ys, label=f"{device_type} ({len(xs)})", color=color, s=40, alpha=0.85)

    ax.set_title("P03.03 sensor catalog - device positions (local plane, meters)")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_xlim(-50, 950)
    ax.set_ylim(-50, 850)
    ax.set_aspect("equal")
    ax.grid(True, linestyle=":", alpha=0.4)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=3, fontsize=8)
    fig.tight_layout()

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    fig.savefig(OUTPUT_PATH, dpi=150)
    print(f"wrote {OUTPUT_PATH} ({len(devices)} devices)")


if __name__ == "__main__":
    main()
