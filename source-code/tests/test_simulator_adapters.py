"""P07.06: the pure TraCI adapter functions (mocked traci module, no real
SUMO) and the Windows->WSL path translation. The real container pipeline -
actual TraCI actions against a real running SUMO instance, idempotency
surviving a fresh container, the full approve->execute->executed/failed
path - is proven by backend/control/verify_simulator_adapters.py
(docs/evidence/p07_06_simulator_adapters.json)."""

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_traci(monkeypatch):
    fake = MagicMock()
    fake.trafficlight.getIDList.return_value = ["int-a2"]
    fake.trafficlight.getPhase.return_value = 0
    fake.trafficlight.getNextSwitch.return_value = 40.0
    fake.simulation.getTime.return_value = 20.0
    fake.lane.getIDList.return_value = ["int-a2_int-a3_2", "int-a2_int-a3_3"]
    fake.lane.getDisallowed.return_value = ("passenger",)
    fake.edge.getIDList.return_value = ["int-a2_int-a3"]
    fake.edge.getLastStepVehicleNumber.return_value = 3
    monkeypatch.setitem(sys.modules, "traci", fake)
    for mod in list(sys.modules):
        if mod.startswith("simulator.control_adapters.adapters") or mod == "adapters":
            del sys.modules[mod]
    sys.path.insert(
        0,
        str(
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "simulator"
            / "control_adapters"
        ),
    )
    import adapters

    return fake, adapters


def test_signal_adapter_rejects_an_unregistered_traffic_light(fake_traci):
    fake, adapters = fake_traci
    with pytest.raises(adapters.AdapterError):
        adapters.apply_signal("not-a-real-tl", 10.0, 20.0)


def test_signal_adapter_clamps_the_deviation_to_the_caller_supplied_bound(fake_traci):
    fake, adapters = fake_traci
    fake.trafficlight.getNextSwitch.side_effect = [
        40.0,
        40.0 + 20.0,
    ]  # before, after (server clamps at max)
    adapters.apply_signal("int-a2", 999.0, 20.0)
    fake.trafficlight.setPhaseDuration.assert_called_once()
    args = fake.trafficlight.setPhaseDuration.call_args[0]
    assert (
        args[0] == "int-a2" and abs(args[1] - (20.0 + 20.0)) < 1e-6
    )  # remaining (40-20=20) + clamped 20s deviation


def test_diversion_adapter_rejects_an_edge_with_no_general_lanes(fake_traci):
    fake, adapters = fake_traci
    fake.lane.getIDList.return_value = []
    with pytest.raises(adapters.AdapterError):
        adapters.apply_diversion("int-a2_int-a3")


def test_diversion_adapter_closes_every_general_lane_and_reports_what_it_observed(fake_traci):
    fake, adapters = fake_traci
    result = adapters.apply_diversion("int-a2_int-a3")
    assert fake.lane.setDisallowed.call_count == 2
    assert result["lanes_closed"] == ["int-a2_int-a3_2", "int-a2_int-a3_3"]


def test_vms_adapter_never_calls_a_simulator_actuation_function_and_says_so(fake_traci):
    fake, adapters = fake_traci
    result = adapters.apply_vms("int-a2_int-a3", "expect delays")
    assert (
        result["simulator_actuation"] is False and "no native SUMO VMS API" in result["limitation"]
    )
    fake.lane.setDisallowed.assert_not_called()
    fake.trafficlight.setPhaseDuration.assert_not_called()


def test_vms_adapter_rejects_an_unknown_edge(fake_traci):
    fake, adapters = fake_traci
    fake.edge.getIDList.return_value = []
    with pytest.raises(adapters.AdapterError):
        adapters.apply_vms("not-a-real-edge", "x")


def test_wsl_path_translation_maps_drive_letters_to_mnt() -> None:
    from backend.control.simulator_adapters import RUN_SCRIPT

    posix = str(RUN_SCRIPT).replace("\\", "/").replace("D:/", "/mnt/d/").replace("C:/", "/mnt/c/")
    assert not posix[1:3] == ":/" and "run_container.sh" in posix
