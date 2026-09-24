import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/usr/share/sumo/tools")
import traci

NET = "/work/simulator/network/output/district.net.xml"
scratch = Path(tempfile.mkdtemp(prefix="proto-"))
rou_file = scratch / "t.rou.xml"
sumocfg_file = scratch / "t.sumocfg"
rou_file.write_text("""<routes>
<vType id="passenger" vClass="passenger" length="4.5" maxSpeed="16.7"/>
<route id="r1" edges="int-a1_int-a2 int-a2_int-a3 int-a3_int-a4"/>
<vehicle id="v0" type="passenger" route="r1" depart="0"/>
<vehicle id="v1" type="passenger" route="r1" depart="2"/>
</routes>""")
sumocfg_file.write_text(f'''<configuration>
<input><net-file value="{NET}"/><route-files value="{rou_file}"/></input>
<time><begin value="0"/><end value="60"/></time>
</configuration>''')
traci.start(["sumo", "-c", str(sumocfg_file), "--no-step-log", "--start"])
for _ in range(20):
    traci.simulationStep()
tls = traci.trafficlight.getIDList()
print("tls", tls[:5], len(tls))
tl = "int-a2"
print(
    "phase",
    traci.trafficlight.getPhase(tl),
    "next_switch",
    traci.trafficlight.getNextSwitch(tl),
    "now",
    traci.simulation.getTime(),
)
traci.trafficlight.setPhaseDuration(tl, 40.0)
print("after set next_switch", traci.trafficlight.getNextSwitch(tl))
edges = traci.edge.getIDList()
print("edge sample", [e for e in edges if not e.startswith(":")][:3])
e = "int-a2_int-a3"
print("allowed before", traci.lane.getAllowed(e + "_2"))
traci.lane.setDisallowed(e + "_2", ["passenger"])
print("disallowed after", traci.lane.getDisallowed(e + "_2"))
traci.close()
print("OK")
