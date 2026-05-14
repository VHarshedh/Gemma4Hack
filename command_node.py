#!/usr/bin/env python3
"""
Aegis — Command Center (Node B): Gemma 4 31B with Function Calling
===================================================================
FastAPI server that receives field reports from Node A, reasons about
them using Gemma 4 31B Dense (via llama-cpp-python), executes tool
calls against the local GIS SQLite database, and produces a structured
dispatch plan.

Function-Calling Architecture
-----------------------------
Gemma 4 uses a specialised token format for tool invocation:

  1. Tools are defined as JSON schemas in the system prompt.
  2. The model outputs ``<tool_call>`` delimited JSON when it needs data.
  3. We parse the call, execute it against SQLite, and inject the result
     as a ``<tool_response>`` message.
  4. The model then generates the final dispatch plan.

The ``<think>`` block is enabled so the model reasons step-by-step
before deciding which tool to call.

Usage
-----
    python command_node.py              # start server on :8091
    python command_node.py --mock       # use mock LLM (no weights)
"""
from __future__ import annotations

import argparse, json, logging, math, re, sqlite3, time, textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from config import (
    COMMAND_MODEL_PATH, COMMAND_NODE_HOST, COMMAND_NODE_PORT,
    CONTEXT_SIZE, DATABASE_PATH, MAX_TOKENS, TEMPERATURE, TOP_K, TOP_P,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s|%(name)-16s|%(levelname)-7s|%(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("aegis.command")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Tool Definitions (JSON Schema for Gemma 4 function calling)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  GIS Database Query Executor
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km between two GPS points."""
    R = 6371.0
    dlat, dlon = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


class GISDatabase:
    """Thin wrapper around the local SQLite GIS database."""

    def __init__(self, db_path: Path):
        if not db_path.exists():
            raise FileNotFoundError(f"Database not found: {db_path}. Run setup_db.py first.")
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def query_safe_zones(self, latitude: float, longitude: float, radius_km: float = 5.0, zone_type: str = "any") -> list[dict]:
        conn = self._conn()
        query = "SELECT * FROM safe_zones WHERE status != 'offline'"
        if zone_type != "any":
            query += f" AND type = '{zone_type}'"
        rows = conn.execute(query).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = _haversine(latitude, longitude, r["latitude"], r["longitude"])
            if d <= radius_km:
                results.append({**dict(r), "distance_km": round(d, 2), "remaining_capacity": r["capacity"] - r["current_occupancy"]})
        return sorted(results, key=lambda x: x["distance_km"])

    def query_hazards(self, latitude: float, longitude: float, radius_km: float = 5.0, min_severity: str = "low") -> list[dict]:
        sev_order = {"low": 0, "moderate": 1, "high": 2, "critical": 3}
        min_sev = sev_order.get(min_severity, 0)
        conn = self._conn()
        rows = conn.execute("SELECT * FROM hazards").fetchall()
        conn.close()
        results = []
        for r in rows:
            if sev_order.get(r["severity"], 0) < min_sev:
                continue
            d = _haversine(latitude, longitude, r["latitude"], r["longitude"])
            if d <= radius_km:
                results.append({**dict(r), "distance_km": round(d, 2)})
        return sorted(results, key=lambda x: x["distance_km"])

    def query_routes(self, from_lat: float, from_lon: float, to_lat: float | None = None, to_lon: float | None = None, status_filter: str = "any") -> list[dict]:
        conn = self._conn()
        rows = conn.execute("SELECT * FROM routes").fetchall()
        conn.close()
        results = []
        for r in rows:
            if status_filter not in ("any", r["status"]):
                continue
            d_from = _haversine(from_lat, from_lon, r["from_lat"], r["from_lon"])
            if d_from <= 3.0:
                entry = {**dict(r), "proximity_to_origin_km": round(d_from, 2)}
                if to_lat and to_lon:
                    entry["proximity_to_dest_km"] = round(_haversine(to_lat, to_lon, r["to_lat"], r["to_lon"]), 2)
                results.append(entry)
        return sorted(results, key=lambda x: x["proximity_to_origin_km"])

    def query_sop(self, query: str) -> list[dict]:
        conn = self._conn()
        rows = conn.execute("SELECT title, content FROM sops WHERE sops MATCH ? ORDER BY rank LIMIT 3", (query,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def store_field_report(self, report: dict) -> int:
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO field_reports (operator_id,timestamp,latitude,longitude,audio_transcript,image_analysis,threat_level,category,raw_payload) VALUES (?,?,?,?,?,?,?,?,?)",
            (report.get("operator_id"), report.get("timestamp"), report.get("location",{}).get("latitude"), report.get("location",{}).get("longitude"),
             report.get("audio_transcript"), report.get("image_analysis"), report.get("threat_level"), report.get("category"), json.dumps(report)),
        )
        conn.commit()
        rid = cur.lastrowid
        conn.close()
        return rid

    def execute_tool(self, name: str, arguments: dict) -> Any:
        dispatch = {"query_safe_zones": self.query_safe_zones, "query_hazards": self.query_hazards, "query_routes": self.query_routes, "query_sop": self.query_sop}
        fn = dispatch.get(name)
        if not fn:
            return {"error": f"Unknown tool: {name}"}
        try:
            return fn(**arguments)
        except Exception as e:
            return {"error": str(e)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LLM Backends
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SYSTEM_PROMPT = textwrap.dedent("""\
You are AEGIS, an AI crisis coordinator for disaster response. You operate
in a zero-internet environment and must make life-safety decisions using
ONLY the local GIS database accessible through the tools provided.

CRITICAL RULES:
1. ALWAYS query the database before making routing decisions.
2. NEVER recommend routes through known hazards.
3. Prioritise hospitals for injured, shelters for displaced civilians.
4. Factor in remaining capacity — do not send people to full shelters.
5. Use <think> tags to reason step-by-step before calling tools or giving
   your final answer.

You have access to the following tools:
{tools}

When you need to call a tool, respond with EXACTLY this JSON format
on its own line:
<tool_call>{{"name": "<function_name>", "arguments": {{...}}}}</tool_call>

After receiving tool results, synthesise them into a DISPATCH PLAN with:
- Recommended safe zone (with remaining capacity)
- Primary and alternate evacuation routes
- Hazards to avoid along each route
- Estimated travel time
- Priority level and recommended response teams
""")

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


class LLMBackend:
    """llama-cpp-python backend for Gemma 4 31B Dense."""

    def __init__(self, model_path: Path):
        from llama_cpp import Llama
        log.info("Loading Gemma 4 31B from %s (this may take a minute) …", model_path)
        self.llm = Llama(
            model_path=str(model_path), n_ctx=CONTEXT_SIZE, n_gpu_layers=-1,
            verbose=False, chat_format="gemma",
        )
        log.info("Model loaded.")

    def generate(self, messages: list[dict]) -> str:
        resp = self.llm.create_chat_completion(
            messages=messages, temperature=TEMPERATURE, top_p=TOP_P, top_k=TOP_K,
            max_tokens=MAX_TOKENS,
        )
        return resp["choices"][0]["message"]["content"]


class MockLLMBackend:
    """Deterministic mock that simulates the full tool-calling loop."""

    def __init__(self):
        log.info("[MOCK] Using mock LLM backend — no model weights required.")
        self._call_count = 0

    def generate(self, messages: list[dict]) -> str:
        self._call_count += 1

        # First call → simulate thinking + tool calls
        if self._call_count == 1:
            return (
                "<think>\nAnalysing field report. Operator reports structural collapse with gas leak "
                "at ~46.2088, -123.8156. I need to:\n1. Check nearby hazards to understand the threat landscape\n"
                "2. Find safe zones with remaining capacity\n3. Find clear evacuation routes\nLet me query the database.\n</think>\n\n"
                'I need to assess the situation. Let me check the area.\n\n'
                '<tool_call>{"name": "query_hazards", "arguments": {"latitude": 46.2088, "longitude": -123.8156, "radius_km": 3.0, "min_severity": "moderate"}}</tool_call>'
            )
        if self._call_count == 2:
            return '<tool_call>{"name": "query_safe_zones", "arguments": {"latitude": 46.2088, "longitude": -123.8156, "radius_km": 5.0, "zone_type": "any"}}</tool_call>'
        if self._call_count == 3:
            return '<tool_call>{"name": "query_sop", "arguments": {"query": "gas leak"}}</tool_call>'
        if self._call_count == 4:
            return '<tool_call>{"name": "query_routes", "arguments": {"from_lat": 46.2088, "from_lon": -123.8156}}</tool_call>'

        # Final call → dispatch plan
        return textwrap.dedent("""\
            <think>
            [AGENT: HazMat] Analyzed incident. Multiple hazards in the area including a collapsed parking garage and active gas leak. Chemical spill near industrial area requires masks on Route Golf.
            [AGENT: Logistics] Safe zones queried. Cascadia Bay High School has remaining capacity (488 slots) and is operational. Route Alpha from Harbor Park is clear with National Guard escort. Route Delta is BLOCKED due to parking garage collapse.
            [AGENT: Medical] Casualties reported. St. Mary's is at capacity. Triage required on-site before transport.
            [AGENT: Commander] Synthesizing inputs... I should route civilians to the High School via Route Alpha, avoiding the gas leak on 4th & Harbor. Dispatching specialized units.
            </think>

            # 🚨 AEGIS MULTI-AGENT DISPATCH PLAN — PRIORITY: CRITICAL

            ## Swarm Assessment
            - **HazMat Agent**: Detected active gas leak creating ignition risk.
            - **Logistics Agent**: Identified Route Delta as blocked. Route Alpha is the primary evacuation corridor.
            - **Medical Agent**: Prioritizing on-site triage due to limited hospital capacities.

            ## Recommended Actions

            ### 1. Immediate Dispatch
            | Team | Destination | Priority |
            |------|------------|----------|
            | SAR Team (Urban) | Collapse site — 4th & Harbor | IMMEDIATE |
            | HazMat Unit | Gas leak — 4th & Harbor | IMMEDIATE |
            | Ambulance × 2 | Stage at Firehouse #7 | HIGH |

            ### 2. Evacuation Route — PRIMARY
            - **Route**: Route Alpha — Harbor Park → Cascadia Bay High School
            - **Status**: ✅ CLEAR (National Guard escorted)
            - **Distance**: 2.1 km | **ETA**: ~8 minutes
            - **Destination Capacity**: 488 remaining slots

            ### 3. Hazards to AVOID
            - 🚫 Route Delta — BLOCKED (parking garage collapse debris)
            - ⚠️ Keep 150m clearance from 4th & Harbor (gas main rupture)
            - ⚠️ Keep 30m from Oak Street (live 12kV power line)
        """)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Reasoning Engine — Orchestrates the multi-turn tool loop
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class ReasoningEngine:
    """
    Multi-turn orchestrator that manages the think → tool_call → tool_response
    loop between the LLM and the GIS database.
    """

    MAX_TOOL_ROUNDS = 5

    def __init__(self, llm: LLMBackend | MockLLMBackend, gis: GISDatabase):
        self.llm = llm
        self.gis = gis

    def process_report(self, report: dict) -> dict:
        """Process a field report through the full reasoning pipeline."""
        t0 = time.perf_counter()
        tools_json = json.dumps(TOOL_DEFINITIONS, indent=2)
        sys_prompt = SYSTEM_PROMPT.format(tools=tools_json)

        user_msg = (
            f"INCOMING FIELD REPORT — PRIORITY: {report.get('threat_level','UNKNOWN').upper()}\n\n"
            f"Operator: {report.get('operator_id')}\n"
            f"Time: {report.get('timestamp')}\n"
            f"Location: {report.get('location',{}).get('latitude')}, {report.get('location',{}).get('longitude')}\n"
            f"Category: {report.get('category')}\n\n"
            f"AUDIO TRANSCRIPT:\n{report.get('audio_transcript','N/A')}\n\n"
            f"VISUAL ASSESSMENT:\n{report.get('image_analysis','N/A')}\n\n"
            "Analyse the situation, query the GIS database for safe zones, hazards, "
            "and evacuation routes, then produce a DISPATCH PLAN."
        )

        messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_msg}]
        tool_calls_log = []

        for round_num in range(1, self.MAX_TOOL_ROUNDS + 1):
            log.info("LLM reasoning round %d/%d …", round_num, self.MAX_TOOL_ROUNDS)
            response = self.llm.generate(messages)
            log.info("LLM response (%d chars)", len(response))

            tool_match = TOOL_CALL_RE.search(response)
            if not tool_match:
                # No tool call — this is the final response
                elapsed = time.perf_counter() - t0
                return {
                    "status": "success",
                    "dispatch_plan": response,
                    "tool_calls": tool_calls_log,
                    "reasoning_rounds": round_num,
                    "processing_time_s": round(elapsed, 2),
                }

            # Parse and execute tool call
            try:
                call = json.loads(tool_match.group(1))
            except json.JSONDecodeError:
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": "Error: malformed tool call JSON. Please try again."})
                continue

            tool_name = call.get("name", "")
            tool_args = call.get("arguments", {})
            log.info("Tool call: %s(%s)", tool_name, json.dumps(tool_args))

            result = self.gis.execute_tool(tool_name, tool_args)
            tool_calls_log.append({"round": round_num, "tool": tool_name, "arguments": tool_args, "result_count": len(result) if isinstance(result, list) else 1})
            log.info("Tool returned %s results", len(result) if isinstance(result, list) else 1)

            # Feed results back to model
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"<tool_response>\n{json.dumps(result, indent=2, default=str)}\n</tool_response>\n\nContinue your analysis. Call another tool if needed, or provide the final DISPATCH PLAN."})

        elapsed = time.perf_counter() - t0
        return {"status": "max_rounds_reached", "dispatch_plan": response, "tool_calls": tool_calls_log, "reasoning_rounds": self.MAX_TOOL_ROUNDS, "processing_time_s": round(elapsed, 2)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FastAPI Application
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class FieldReportPayload(BaseModel):
    report_id: str = ""
    operator_id: str = ""
    timestamp: str = ""
    location: dict = Field(default_factory=dict)
    audio_transcript: str = ""
    image_analysis: str = ""
    threat_level: str = "unknown"
    category: str = "unclassified"
    confidence: float = 0.0
    raw_audio_duration_s: float = 0.0
    model_backend: str = "unknown"

EVENTS_STORE = []
templates = Jinja2Templates(directory="templates")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()


def create_app(use_mock: bool = False) -> FastAPI:
    app = FastAPI(title="Aegis Command Center", description="Node B — Gemma 4 31B Crisis Coordinator", version="1.0.0")
    
    # Mount static files for JS and CSS
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # Initialise backends
    gis = GISDatabase(DATABASE_PATH)
    if use_mock or not COMMAND_MODEL_PATH.exists():
        if not use_mock:
            log.warning("Model not found at %s — using mock backend.", COMMAND_MODEL_PATH)
        llm = MockLLMBackend()
    else:
        llm = LLMBackend(COMMAND_MODEL_PATH)
    engine = ReasoningEngine(llm, gis)

    @app.get("/", response_class=HTMLResponse)
    def root(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"events": EVENTS_STORE}
        )

    @app.get("/api/v1/events")
    async def get_events():
        return JSONResponse({"events": EVENTS_STORE})

    @app.websocket("/api/v1/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(websocket)

    @app.get("/api/v1/health")
    def health():
        return {"status": "healthy", "database": str(DATABASE_PATH), "model_loaded": not isinstance(llm, MockLLMBackend), "timestamp": datetime.now(timezone.utc).isoformat()}

    @app.post("/api/v1/field-report")
    async def receive_field_report(payload: FieldReportPayload):
        log.info("Received field report %s from %s", payload.report_id, payload.operator_id)
        report_dict = payload.model_dump()

        # Store in database
        try:
            db_id = gis.store_field_report(report_dict)
            log.info("Stored as field_report #%d", db_id)
        except Exception as e:
            log.error("Failed to store report: %s", e)
            db_id = None

        # Process through reasoning engine
        result = engine.process_report(report_dict)
        
        # Save to memory for dashboard
        event_record = {
            "report": report_dict,
            "result": result
        }
        EVENTS_STORE.append(event_record)
        
        await manager.broadcast({"events": EVENTS_STORE})
        
        return {"report_id": payload.report_id, "db_record_id": db_id, **result}

    @app.get("/api/v1/safe-zones")
    def list_safe_zones(lat: float = 46.21, lon: float = -123.82, radius: float = 10.0):
        return gis.query_safe_zones(lat, lon, radius)

    @app.get("/api/v1/hazards")
    def list_hazards(lat: float = 46.21, lon: float = -123.82, radius: float = 10.0):
        return gis.query_hazards(lat, lon, radius)

    @app.get("/api/v1/routes")
    def list_routes(from_lat: float = 46.21, from_lon: float = -123.82):
        return gis.query_routes(from_lat, from_lon)

    return app


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Entry Point
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    parser = argparse.ArgumentParser(description="Aegis Command Center — Node B")
    parser.add_argument("--mock", action="store_true", help="Use mock LLM backend")
    parser.add_argument("--host", default=COMMAND_NODE_HOST)
    parser.add_argument("--port", type=int, default=COMMAND_NODE_PORT)
    args = parser.parse_args()

    app = create_app(use_mock=args.mock)
    log.info("Starting Aegis Command Center on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
