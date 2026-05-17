"""
Aegis — Server Response Helpers
===============================
Logic for checking sensor thresholds and building voice UI context.
"""
import logging

log = logging.getLogger("aegis.server.helpers")

SENSOR_THRESHOLDS = {
    # AQI: 0-50 Good, 51-100 Moderate, 101-200 Unhealthy, 201+ Very Unhealthy/Hazardous
    "air_quality": {"critical": 200, "high": 100, "unit": "AQI"},
    # Mw: 3.5 felt/minor damage, 5.0+ significant structural damage
    "seismic":     {"critical": 5.0, "high": 3.5, "unit": "Mw"},
    # metres: 0.8 watch level, 1.5 danger/evacuation level
    "flood":       {"critical": 1.5, "high": 0.8, "unit": "m"},
    # °C: 250 active fire proximity, 400 extreme/structure threat
    "fire":        {"critical": 400, "high": 250, "unit": "°C"},
}

def check_sensor_threshold(payload) -> dict | None:
    """Return an alert dict if a sensor reading exceeds thresholds."""
    thresholds = SENSOR_THRESHOLDS.get(payload.type)
    if not thresholds:
        return None
    if payload.value >= thresholds["critical"]:
        return {
            "severity": "critical", 
            "sensor_id": payload.sensor_id, 
            "type": payload.type,
            "value": payload.value, 
            "description": f"CRITICAL: {payload.type} sensor {payload.sensor_id} at {payload.value} {thresholds['unit']}"
        }
    if payload.value >= thresholds["high"]:
        return {
            "severity": "high", 
            "sensor_id": payload.sensor_id, 
            "type": payload.type,
            "value": payload.value, 
            "description": f"HIGH: {payload.type} sensor {payload.sensor_id} at {payload.value} {thresholds['unit']}"
        }
    return None


def build_voice_context(question: str, gis, events_store: list) -> str:
    """
    Build a situational context string from events_store and GIS data.
    """
    parts = []

    # Active events summary
    if events_store:
        parts.append(f"ACTIVE INCIDENTS: {len(events_store)} reports on file.")
        for i, ev in enumerate(events_store[-5:], 1):  # last 5
            r = ev.get("report", {})
            parts.append(
                f"  #{i}: {r.get('category','?')} at ({r.get('location',{}).get('latitude','?')},"
                f" {r.get('location',{}).get('longitude','?')}) — {r.get('threat_level','?').upper()}"
            )
    else:
        parts.append("ACTIVE INCIDENTS: No reports received yet.")

    # Pull live GIS data relevant to the question
    q = question.lower()
    try:
        if any(w in q for w in ("hospital", "capacity", "medical", "triage")):
            zones = gis.query_safe_zones(46.21, -123.82, 10.0, "hospital")
            parts.append("HOSPITAL STATUS:")
            for z in zones:
                remaining = z.get("remaining_capacity", z.get("capacity", 0) - z.get("current_occupancy", 0))
                parts.append(f"  {z['name']}: {remaining} slots remaining [{z['status']}]")

        elif any(w in q for w in ("shelter", "safe zone", "evacuate", "capacity")):
            zones = gis.query_safe_zones(46.21, -123.82, 10.0, "any")
            parts.append("SAFE ZONES:")
            for z in zones[:5]:
                remaining = z.get("remaining_capacity", z.get("capacity", 0) - z.get("current_occupancy", 0))
                parts.append(f"  {z['name']} ({z['type']}): {remaining} slots [{z['status']}]")

        elif any(w in q for w in ("hazard", "danger", "threat", "gas", "chemical", "fire")):
            hazards = gis.query_hazards(46.21, -123.82, 10.0, "moderate")
            parts.append("ACTIVE HAZARDS:")
            for h in hazards[:5]:
                parts.append(f"  {h['type']} [{h['severity']}]: {h.get('description','N/A')}")

        elif any(w in q for w in ("route", "road", "blocked", "evacuation")):
            routes = gis.query_routes(46.21, -123.82)
            parts.append("EVACUATION ROUTES:")
            for r in routes:
                parts.append(f"  {r['name']} — {r['status'].upper()} ({r['estimated_time_min']} min)")

        else:
            # General situational awareness
            zones = gis.query_safe_zones(46.21, -123.82, 10.0, "any")
            hazards = gis.query_hazards(46.21, -123.82, 5.0, "high")
            parts.append(f"OVERVIEW: {len(zones)} safe zones, {len(hazards)} high+ hazards in area.")
    except Exception as e:
        parts.append(f"GIS QUERY ERROR: {e}")

    return "\n".join(parts)


def mock_voice_response(question: str, context: str, events_store: list) -> str:
    """Intelligent mock voice response that parses intent from the question."""
    q = question.lower()

    if any(w in q for w in ("hospital", "medical", "capacity")):
        return (
            "St. Mary's Hospital is at 86% capacity, accepting critical patients only. "
            "Bayfront Medical Clinic is effectively full. Recommend routing walking wounded "
            "to Cascadia Bay High School where Red Cross personnel are on-site."
        )
    elif any(w in q for w in ("route", "road", "blocked", "evacuation")):
        return (
            "Route Alpha from Harbor Park to the High School is CLEAR with National Guard escort, "
            "ETA 8 minutes. Route Delta is BLOCKED due to parking garage collapse. "
            "Route Golf is partial — requires masks due to chemical spill proximity."
        )
    elif any(w in q for w in ("hazard", "danger", "gas", "chemical", "fire")):
        return (
            "Two critical hazards detected: parking garage collapse at 4th & Harbor with active gas leak, "
            "and industrial solvent spill at Cascadia Chemical. Maintain 150m and 300m exclusion zones respectively."
        )
    elif any(w in q for w in ("status", "update", "sitrep", "situation")):
        n = len(events_store)
        return (
            f"SITREP: {n} field report{'s' if n != 1 else ''} processed. "
            "Multi-agent swarm is active with HazMat, Logistics, and Medical agents online. "
            "All sensor networks are streaming. Primary evacuation corridor is Route Alpha."
        )
    elif any(w in q for w in ("drone", "aerial", "recon")):
        return (
            "Autonomous drone fleet is deployed. Two units are scanning the collapse zone at 4th & Harbor, "
            "one unit is monitoring the chemical spill perimeter at Cascadia Chemical."
        )
    elif any(w in q for w in ("sensor", "iot", "air quality", "seismic", "flood")):
        return (
            "IoT sensor grid is active: 5 AQI sensors, 3 seismic monitors, 3 flood gauges, 2 thermal sensors. "
            "Auto-escalation thresholds are armed. No critical readings in the last cycle."
        )
    else:
        return (
            f"Command acknowledged. {len(events_store)} active incidents being tracked. "
            "All swarm agents are operational. Ask about routes, hazards, shelters, or sensors for details."
        )
