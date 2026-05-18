import asyncio
import httpx
import time
import uuid
import secrets
from datetime import datetime, timezone
import random
from config import COMMAND_NODE_URL

try:
    from faker import Faker
    fake = Faker()
except ImportError:
    print("Please install faker: pip install faker")
    exit(1)


REPORT_COUNT  = 30    # total reports to fire
MONITOR_SECS  = 1800  # how long to watch for completions (30 min)
POLL_INTERVAL = 180   # check dashboard every N seconds (3 min)

# 30 distinct incidents — varied types, realistic transcripts, spread across
# the Cascadia Bay operational area (approx Pacific Northwest coast)
INCIDENTS = [
    # Structural
    {"category": "structural_collapse", "threat_level": "critical",
     "lat": 46.210, "lon": -123.820,
     "audio": "Command, Alpha-One. Parking garage on 4th and Harbor has collapsed. People trapped on upper floors. Gas smell in the area. Request SAR and HazMat immediately.",
     "image": "Multi-storey concrete structure pancaked. Visible survivors on rubble. Active gas leak from exposed pipe."},
    {"category": "structural_collapse", "threat_level": "high",
     "lat": 46.225, "lon": -123.805,
     "audio": "Bravo-Two here. Residential block on Third Avenue is partially collapsed after the tremor. Approximately 20 occupants unaccounted for.",
     "image": "Three-storey residential building with collapsed south wing. Debris field extends 15 metres."},

    # Wildfire
    {"category": "wildfire", "threat_level": "critical",
     "lat": 46.195, "lon": -123.850,
     "audio": "Charlie-Three. Wildfire spreading east from the ridge line. Wind shift incoming. Pacific Ridge sector needs immediate evacuation.",
     "image": "Active crown fire advancing east. Embers spotted 300m ahead of fire line."},
    {"category": "wildfire", "threat_level": "high",
     "lat": 46.180, "lon": -123.870,
     "audio": "Delta-Four. Brush fire near Coastal Highway has jumped the fire break. Two structures alight. Road is cut off.",
     "image": "Two residential structures fully involved. Brush fire moving north-east at speed."},
    {"category": "wildfire", "threat_level": "high",
     "lat": 46.240, "lon": -123.790,
     "audio": "Echo-Five. Forest fire on north ridge, smoke column visible from 10km. Campground in its path, evacuating now.",
     "image": "Dense smoke column rising from forested ridge. Estimated 40 hectares involved."},

    # Flood / Tsunami
    {"category": "flash_flood", "threat_level": "critical",
     "lat": 46.205, "lon": -123.835,
     "audio": "Foxtrot-Six. Flash flood on Coastal Highway. Two vehicles submerged. Road is impassable. Requesting rescue boats.",
     "image": "Floodwater 1.5m deep on highway. Two vehicle rooftops visible. Rapid current."},
    {"category": "flash_flood", "threat_level": "high",
     "lat": 46.215, "lon": -123.800,
     "audio": "Golf-Seven. River has burst its banks at the south bridge. Residential area flooding fast. Elderly residents need evacuation.",
     "image": "River 2m above flood stage. Water entering ground floor of six residences."},
    {"category": "tsunami", "threat_level": "critical",
     "lat": 46.190, "lon": -123.860,
     "audio": "Hotel-Eight. Tsunami warning active. Coastal zones below 10 metres are already flooded. Route Delta is underwater. Use Route Alpha.",
     "image": "Seawater inundating harbour front. Vessels displaced onto road. Evacuation in progress."},
    {"category": "tsunami", "threat_level": "critical",
     "lat": 46.185, "lon": -123.875,
     "audio": "India-Nine. Second wave incoming. Pier has collapsed. Multiple people in the water near the marina.",
     "image": "Pier structure collapsed. Debris field in harbour. People visible clinging to floating wreckage."},

    # Chemical / HazMat
    {"category": "chemical_spill", "threat_level": "critical",
     "lat": 46.220, "lon": -123.810,
     "audio": "Juliet-Ten. Chemical spill at Cascadia Chemical plant. Strong odour, workers evacuating. Need HazMat Level-A response.",
     "image": "Green vapour cloud drifting south-east from industrial facility. Workers in partial evacuation."},
    {"category": "chemical_spill", "threat_level": "high",
     "lat": 46.230, "lon": -123.795,
     "audio": "Kilo-Eleven. Overturned tanker on Route Golf. Unknown liquid pooling on road. Driver is out but dazed. Possible HAZMAT.",
     "image": "Articulated tanker on its side. Unidentified fluid pooling 20m radius. No placard visible."},
    {"category": "gas_leak", "threat_level": "critical",
     "lat": 46.208, "lon": -123.822,
     "audio": "Lima-Twelve. Major gas main rupture at 4th and Harbor. Strong odour across three blocks. Evacuating residents now.",
     "image": "Cracked road surface with visible gas venting. Adjacent building windows blown out."},
    {"category": "gas_leak", "threat_level": "high",
     "lat": 46.218, "lon": -123.808,
     "audio": "Mike-Thirteen. Residential gas leak, single house, occupant reports strong smell and is outside. Low pressure in the line.",
     "image": "Single residential property with gas meter damaged. Occupant safe at perimeter."},

    # Seismic / Earthquake
    {"category": "earthquake", "threat_level": "critical",
     "lat": 46.212, "lon": -123.818,
     "audio": "November-Fourteen. Magnitude 5.8 felt. Harbour pier collapsed, multiple injuries. Structural engineers and triage unit needed.",
     "image": "Pier structure in water. Multiple people on ground near collapsed section. Ambulances on scene."},
    {"category": "earthquake", "threat_level": "high",
     "lat": 46.228, "lon": -123.802,
     "audio": "Oscar-Fifteen. Post-quake damage at the community centre. Roof partially caved in. About 30 people were inside.",
     "image": "Community centre with collapsed roof section. People self-evacuating through side exits."},
    {"category": "earthquake", "threat_level": "high",
     "lat": 46.198, "lon": -123.845,
     "audio": "Papa-Sixteen. Downed power lines on Oak Street after the tremor. Live wires on the road, blocking evacuation.",
     "image": "Three power poles down across road. Sparking cable on wet pavement."},

    # Medical mass-casualty
    {"category": "mass_casualty", "threat_level": "critical",
     "lat": 46.213, "lon": -123.823,
     "audio": "Quebec-Seventeen. Multi-vehicle accident on the highway bridge. At least eight casualties, two unresponsive. Request multiple ambulances.",
     "image": "Five-vehicle pile-up on bridge. Airbags deployed. Two occupants on ground unresponsive."},
    {"category": "mass_casualty", "threat_level": "high",
     "lat": 46.222, "lon": -123.812,
     "audio": "Romeo-Eighteen. Food poisoning outbreak at the harbour festival, over 40 people symptomatic. Medical team needed on site.",
     "image": "Festival grounds with multiple people seated or lying on ground. First aid volunteers overwhelmed."},

    # Infrastructure
    {"category": "bridge_failure", "threat_level": "critical",
     "lat": 46.207, "lon": -123.833,
     "audio": "Sierra-Nineteen. Coastal Road bridge is cracking under flood pressure. One lane has dropped. Closing now but traffic backed up.",
     "image": "Visible crack running across bridge mid-span. Water level 30cm below deck. One lane sagging."},
    {"category": "power_grid_failure", "threat_level": "high",
     "lat": 46.235, "lon": -123.785,
     "audio": "Tango-Twenty. Substation fire has knocked out power to the north district. Hospital is on backup generator.",
     "image": "Substation transformer on fire. Fire crew on scene. Smoke visible from 2km."},

    # Drought / Heatwave
    {"category": "heatwave", "threat_level": "high",
     "lat": 46.202, "lon": -123.840,
     "audio": "Uniform-TwentyOne. Heat index 48C at the outdoor shelter camp. Three elderly evacuees collapsed. Cooling stations needed urgently.",
     "image": "Overcrowded outdoor shelter. No shade structures. Multiple people lying on ground."},
    {"category": "drought", "threat_level": "moderate",
     "lat": 46.245, "lon": -123.775,
     "audio": "Victor-TwentyTwo. Water supply to north sector has failed. Reservoir critically low. Community of 2000 without water.",
     "image": "Reservoir at 8% capacity. Cracked mud visible across former water surface."},

    # Industrial
    {"category": "industrial_explosion", "threat_level": "critical",
     "lat": 46.217, "lon": -123.815,
     "audio": "Whiskey-TwentyThree. Explosion at the harbour warehouse. Multiple casualties visible. Secondary explosions possible — unknown contents.",
     "image": "Large warehouse with roof blown off. Active fire inside. Debris field 50m radius."},
    {"category": "industrial_explosion", "threat_level": "high",
     "lat": 46.226, "lon": -123.800,
     "audio": "X-ray-TwentyFour. Boiler explosion at the fish processing plant. Two workers with burns. Building structurally unsafe.",
     "image": "Processing plant exterior wall blown out. Firefighters establishing perimeter."},

    # Search & Rescue
    {"category": "missing_persons", "threat_level": "high",
     "lat": 46.255, "lon": -123.760,
     "audio": "Yankee-TwentyFive. Group of 12 hikers overdue on the ridge trail. Darkness falling, temperature dropping. Search and rescue needed.",
     "image": "Dense forested ridge terrain. Last known position marked on map. Weather deteriorating."},
    {"category": "missing_persons", "threat_level": "moderate",
     "lat": 46.192, "lon": -123.858,
     "audio": "Zulu-TwentySix. Fishing vessel overdue at harbour. Last contact 6 hours ago. Three crew on board.",
     "image": "Empty berth at harbour. Vessel transponder last pinged 12km offshore."},

    # Civil unrest / crowd crush
    {"category": "crowd_crush", "threat_level": "critical",
     "lat": 46.209, "lon": -123.821,
     "audio": "Alpha-TwentySeven. Crowd crush at the evacuation assembly point. People falling. At least five down and not moving.",
     "image": "Dense crowd at assembly point gate. People pressed against barriers. Several on ground."},

    # Environmental
    {"category": "oil_spill", "threat_level": "high",
     "lat": 46.188, "lon": -123.868,
     "audio": "Bravo-TwentyEight. Oil spill from grounded vessel near the estuary. Slick spreading south. Wildlife sanctuary at risk.",
     "image": "Dark oil slick 200m from shore. Grounded vessel listing 30 degrees. Booms not yet deployed."},
    {"category": "debris_flow", "threat_level": "critical",
     "lat": 46.250, "lon": -123.765,
     "audio": "Charlie-TwentyNine. Landslide on Ridge Road. Mud and debris across highway. Two cars buried. Survivors calling out.",
     "image": "Highway covered 1.5m deep in mud and debris. Vehicle rooftops visible. Active seepage continuing."},
    {"category": "nuclear_alert", "threat_level": "critical",
     "lat": 46.260, "lon": -123.750,
     "audio": "Delta-Thirty. Radiation alarm at the research facility. Possible coolant leak in reactor room. Full evacuation initiated per protocol.",
     "image": "Research facility in lockdown. HazMat teams suiting up at perimeter. No visible external damage."},
]


async def send_report(client, report_id, operator_id, incident: dict):
    payload = {
        "report_id": report_id,
        "operator_id": operator_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "location": {"latitude": incident["lat"], "longitude": incident["lon"]},
        "audio_transcript": incident["audio"],
        "image_analysis": incident["image"],
        "threat_level": incident["threat_level"],
        "category": incident["category"],
        "confidence": round(random.uniform(0.85, 0.99), 2),
        "raw_audio_duration_s": 10.0,
        "model_backend": "chaos_monkey"
    }

    url = f"{COMMAND_NODE_URL}/api/v1/field-report"
    try:
        # Spread out requests slightly to mimic real traffic
        await asyncio.sleep(random.uniform(0.1, 3.0))
        response = await client.post(url, json=payload, timeout=30.0)
        return response.status_code
    except Exception as e:
        return str(e)


async def monitor_completions(client, accepted: int, start: float):
    """Poll /api/v1/events and print a live resolution table until timeout."""
    print(f"\n⏱  Monitoring for {MONITOR_SECS}s — polling every {POLL_INTERVAL}s ...\n")
    url = f"{COMMAND_NODE_URL}/api/v1/events"
    last_resolved = -1

    try:
        while True:
            elapsed = time.time() - start
            if elapsed >= MONITOR_SECS:
                break
            try:
                resp = await client.get(url, timeout=10.0)
                events = resp.json().get("events", [])
                resolved = sum(
                    1 for e in events
                    if isinstance(e.get("result"), dict)
                    and e["result"].get("status") == "success"
                )
                processing = sum(
                    1 for e in events
                    if isinstance(e.get("result"), dict)
                    and e["result"].get("status") in ("processing", "synthesising", None)
                )
                errors = sum(
                    1 for e in events
                    if isinstance(e.get("result"), dict)
                    and e["result"].get("status") == "error"
                )
                # Always print so the terminal shows the script is alive
                marker = "🆕" if resolved != last_resolved else "  "
                print(f"  {marker}[{int(elapsed):>4}s]  ✅ resolved: {resolved}/{accepted}"
                      f"  |  ⏳ processing: {processing}"
                      f"  |  ❌ errors: {errors}")
                last_resolved = resolved
                if resolved + errors >= accepted:
                    break
            except Exception as e:
                print(f"  [{int(elapsed):>4}s]  poll error: {e}")
            await asyncio.sleep(POLL_INTERVAL)
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("\n⚠️  Interrupted — printing partial summary ...")

    elapsed = time.time() - start
    # Final summary
    try:
        resp = await client.get(url, timeout=10.0)
        events = resp.json().get("events", [])
        resolved = sum(
            1 for e in events
            if isinstance(e.get("result"), dict)
            and e["result"].get("status") == "success"
        )
        errors = sum(
            1 for e in events
            if isinstance(e.get("result"), dict)
            and e["result"].get("status") == "error"
        )
        still_processing = max(accepted - resolved - errors, 0)
    except Exception:
        resolved = errors = still_processing = "?"

    print(f"\n{'='*58}")
    print(f"  ⏱  Elapsed : {int(elapsed)}s  (window: {MONITOR_SECS}s)")
    print(f"  📨 Accepted: {accepted}/{REPORT_COUNT}")
    print(f"  ✅ Resolved: {resolved}  |  ❌ Errors: {errors}"
          f"  |  ⏳ Still queued: {still_processing}")
    print(f"{'='*58}")


async def main():
    print("🚀 AEGIS DISASTER TRAFFIC SIMULATOR 🚀")
    print(f"Firing {len(INCIDENTS)} distinct incidents at {COMMAND_NODE_URL} ...")
    print(f"Monitoring completions for {MONITOR_SECS}s ({MONITOR_SECS//60} min). Watch the dashboard!\n")
    for i, inc in enumerate(INCIDENTS, 1):
        print(f"  #{i:02d}  [{inc['threat_level'].upper():<8}]  {inc['category']}")
    print()

    start = time.time()
    async with httpx.AsyncClient() as client:
        tasks = [
            send_report(
                client,
                f"chaos-{uuid.uuid4().hex[:6]}",
                inc["audio"].split(".")[0].split(",")[0].strip(),  # use operator callsign from transcript
                inc,
            )
            for inc in INCIDENTS
        ]
        results = await asyncio.gather(*tasks)

        accepted = sum(1 for r in results if r in (200, 202))
        failed   = [r for r in results if r not in (200, 202)]
        print(f"✅ {accepted}/{len(INCIDENTS)} accepted  |  ❌ {len(failed)} rejected/failed")
        if failed:
            print(f"   Failures: {failed[:5]}{'...' if len(failed) > 5 else ''}")

        await monitor_completions(client, accepted, start)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
