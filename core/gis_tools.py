"""
Aegis — GIS Tool Definitions
============================
JSON schemas for Gemma 4 function calling.
"""

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "query_safe_zones",
            "description": "Search the local GIS database for operational safe zones (shelters, hospitals, staging areas) near a GPS coordinate. Returns zones sorted by distance with capacity info.",
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude":  {"type": "number", "description": "GPS latitude of the search center"},
                    "longitude": {"type": "number", "description": "GPS longitude of the search center"},
                    "radius_km": {"type": "number", "description": "Search radius in kilometres", "default": 5.0},
                    "zone_type": {"type": "string", "enum": ["shelter","hospital","staging_area","fire_station","any"], "description": "Filter by zone type", "default": "any"},
                },
                "required": ["latitude", "longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_hazards",
            "description": "Retrieve known hazards (collapsed structures, gas leaks, floods, chemical spills, fires) near a GPS coordinate from the local GIS database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude":  {"type": "number", "description": "GPS latitude"},
                    "longitude": {"type": "number", "description": "GPS longitude"},
                    "radius_km": {"type": "number", "description": "Search radius in kilometres", "default": 5.0},
                    "min_severity": {"type": "string", "enum": ["low","moderate","high","critical"], "description": "Minimum severity filter", "default": "low"},
                },
                "required": ["latitude", "longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_routes",
            "description": "Find evacuation routes from a location to the nearest safe zones. Returns route status, distance, and estimated travel time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_lat": {"type": "number", "description": "Origin latitude"},
                    "from_lon": {"type": "number", "description": "Origin longitude"},
                    "to_lat":   {"type": "number", "description": "Destination latitude (optional — omit to find all routes from origin)"},
                    "to_lon":   {"type": "number", "description": "Destination longitude (optional)"},
                    "status_filter": {"type": "string", "enum": ["clear","partial","any"], "default": "any"},
                },
                "required": ["from_lat", "from_lon"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_sop",
            "description": "Search the Standard Operating Procedures (SOPs) manual for guidelines on handling specific situations (e.g. hazmat, collapse, tsunami).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keywords (e.g., 'gas leak' or 'chemical spill')"},
                },
                "required": ["query"],
            },
        },
    },
]
