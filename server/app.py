"""
Aegis — Command Center App Factory
==================================
FastAPI application factory with all Aegis routes.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from config import (
    COMMAND_MODEL_PATH, COMMAND_MODEL_PATH_LITE, DATABASE_PATH, USE_POSTGIS,
)
from core.models import FieldReportPayload, SensorDataPayload, VoiceCommandPayload
from core.gis_sqlite import GISDatabase
from core.llm import LLMBackend, MockLLMBackend, OllamaBackend
from core.multi_agent import MultiAgentEngine
from server.ws import manager
from server.mqtt import mqtt_listener
from server.voice_utils import (
    check_sensor_threshold, build_voice_context, mock_voice_response
)

log = logging.getLogger("aegis.server.app")
templates = Jinja2Templates(directory="templates")

# In-memory store for active events (dashboard)
EVENTS_STORE = []

# Limit how many field reports run through the full LLM swarm at once.
# Kept at 1 so only one report's swarm is active at a time — combined with
# OllamaBackend.Semaphore(1) this ensures a single LLM call runs at a time,
# preventing RAM overflow on CPU-only / low-memory machines.
_PROCESSING_SEMAPHORE: asyncio.Semaphore | None = None

def create_app(
    use_mock: bool = False,
    use_lite: bool = False,
    use_ollama: bool = False,
    ollama_model: str | None = None,
) -> FastAPI:

    # 1. Initialise GIS backend
    if USE_POSTGIS:
        from core.gis_postgis import PostGISDatabase
        gis = PostGISDatabase()
        log.info("Using PostGIS spatial backend.")
    else:
        gis = GISDatabase(DATABASE_PATH)
        log.info("Using SQLite GIS backend.")

    # 2. Initialise LLM backend
    # Priority: --mock > --ollama > --lite > default GGUF > auto mock fallback
    if use_mock:
        llm = MockLLMBackend()
        llm_label = "Mock (no model)"
    elif use_ollama:
        from config import OLLAMA_MODEL
        _model = ollama_model or OLLAMA_MODEL
        llm = OllamaBackend(model=_model)
        llm_label = f"Gemma 4 via Ollama ({_model})"
    elif use_lite:
        if COMMAND_MODEL_PATH_LITE.exists():
            log.info("Lite mode: loading Gemma 4 E2B from %s", COMMAND_MODEL_PATH_LITE)
            llm = LLMBackend(COMMAND_MODEL_PATH_LITE)
            llm_label = "Gemma 4 E2B (GGUF lite)"
        else:
            log.warning(
                "Lite model not found at %s — falling back to mock.\n"
                "  Download: https://huggingface.co/google/gemma-4-E2B-it-GGUF\n"
                "  Place as: models/gemma-4-E2B-it-Q4_K_M.gguf",
                COMMAND_MODEL_PATH_LITE,
            )
            llm = MockLLMBackend()
            llm_label = "Mock (lite model not found)"
    elif COMMAND_MODEL_PATH.exists():
        log.info("Loading Gemma 4 27B from %s", COMMAND_MODEL_PATH)
        llm = LLMBackend(COMMAND_MODEL_PATH)
        llm_label = "Gemma 4 27B (GGUF)"
    else:
        log.warning(
            "Model not found at %s — falling back to mock.\n"
            "  For CPU/low-RAM machines run with: --lite\n"
            "  For no model at all run with:      --mock",
            COMMAND_MODEL_PATH,
        )
        llm = MockLLMBackend()
        llm_label = "Mock (model not found)"

    # 3. Initialise reasoning engine (Multi-Agent swarm)
    # use_mock=True only when running the deterministic MockLLMBackend
    _engine_mock = isinstance(llm, MockLLMBackend)
    engine = MultiAgentEngine(llm, gis, use_mock=_engine_mock)
    log.info("Multi-Agent Swarm Engine active.")

    # 4. Lifespan: start MQTT listener on startup, cancel on shutdown.
    #    Skipped when using the mock LLM — no real sensor network is present.
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _PROCESSING_SEMAPHORE
        _PROCESSING_SEMAPHORE = asyncio.Semaphore(1)
        task = None
        if not _engine_mock:
            task = asyncio.create_task(mqtt_listener(gis, manager))
            log.info("MQTT listener started.")
        else:
            log.info("Mock mode — MQTT listener disabled.")
        yield
        if task:
            task.cancel()

    app = FastAPI(
        title="Aegis Command Center",
        description="Node B — Gemma 4 31B Crisis Coordinator",
        version="2.0.0",
        lifespan=lifespan,
    )

    # Mount static files
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # ── Routes ──────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    def root(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"events": EVENTS_STORE, "llm_label": llm_label}
        )

    @app.get("/portal", response_class=HTMLResponse)
    def portal(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="portal.html",
            context={"events": EVENTS_STORE, "llm_label": llm_label}
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
            pass
        finally:
            # Runs on normal disconnect, CancelledError (uvicorn shutdown),
            # or any other exception — prevents stale entries in the manager.
            manager.disconnect(websocket)

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "healthy",
            "database": str(DATABASE_PATH),
            "model_loaded": not isinstance(llm, MockLLMBackend),
            "llm_backend": llm_label,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    @app.post("/api/v1/field-report", status_code=202)
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

        # Show the report on the dashboard immediately as "processing" so the
        # operator sees it arrive without waiting for the full LLM pipeline.
        event_record = {"report": report_dict, "result": {"status": "processing"}}
        EVENTS_STORE.append(event_record)
        await manager.broadcast({"events": EVENTS_STORE})

        # Fire-and-forget: process the LLM pipeline in the background so the
        # field node gets an immediate 202 Accepted instead of waiting 90+ s.
        # The dashboard updates via WebSocket when the pipeline completes.
        async def _process_in_background():
            # Guard: lifespan may not have run (e.g. in TestClient without
            # context manager), so create a one-off semaphore as fallback.
            _sem = _PROCESSING_SEMAPHORE if _PROCESSING_SEMAPHORE is not None \
                else asyncio.Semaphore(1)
            async with _sem:
                log.info("Swarm started for %s (slot acquired)", payload.report_id)
                loop = asyncio.get_running_loop()

                def progress_cb(update: dict):
                    """Called from the executor thread — must not await directly."""
                    stage = update.get("stage")
                    if stage == "specialist_done":
                        agent_progress = event_record["result"].setdefault("agent_progress", {})
                        agent_progress[update["agent"]] = {
                            "tool_calls": update["tool_calls"],
                            "processing_time_s": update["processing_time_s"],
                        }
                    elif stage == "commander_start":
                        event_record["result"]["status"] = "synthesising"
                    # Thread-safe: schedule the coroutine on the event loop
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast({"events": EVENTS_STORE}), loop
                    )

                try:
                    result = await loop.run_in_executor(
                        None, partial(engine.process_report, report_dict, progress_cb)
                    )
                    event_record["result"] = result
                except Exception as e:
                    log.error("Multi-agent processing failed for %s: %s", payload.report_id, e)
                    event_record["result"] = {"status": "error", "error": str(e)}
                finally:
                    await manager.broadcast({"events": EVENTS_STORE})

        asyncio.create_task(_process_in_background())

        return JSONResponse(
            status_code=202,
            content={
                "report_id": payload.report_id,
                "db_record_id": db_id,
                "status": "processing",
                "detail": "Report accepted. Dispatch plan will appear on dashboard when ready.",
            },
        )

    @app.post("/api/v1/sensor-data")
    async def receive_sensor_data(payload: SensorDataPayload):
        data = payload.model_dump()
        data["msg_type"] = "sensor"

        # Store in PostGIS if available
        if USE_POSTGIS and hasattr(gis, 'store_sensor_reading'):
            try:
                gis.store_sensor_reading(
                    payload.sensor_id, payload.latitude, payload.longitude,
                    payload.type, payload.value, payload.unit,
                )
            except Exception as e:
                log.error("Failed to store sensor reading: %s", e)

        # Auto-escalate dangerous readings to hazard alerts
        alert = check_sensor_threshold(payload)
        if alert:
            data["alert"] = alert
            log.warning("SENSOR ALERT: %s", alert["description"])

        await manager.broadcast(data)
        return {"status": "success"}

    @app.post("/api/v1/voice-command")
    async def process_voice_command(payload: VoiceCommandPayload):
        text = payload.text.strip()
        log.info("Voice command received: %s", text)

        # Build situational context from active events + GIS
        context = build_voice_context(text, gis, EVENTS_STORE)

        # Route through LLM for intelligent response (or mock)
        if _engine_mock:
            response = mock_voice_response(text, context, EVENTS_STORE)
        else:
            voice_prompt = (
                "You are AEGIS, a crisis coordination AI. A Commander has asked "
                "a question via voice. Answer concisely (1-3 sentences) based on "
                "the situational context below.\n\n"
                f"CONTEXT:\n{context}\n\n"
                f"COMMANDER QUESTION: {text}\n\n"
                "Respond with a clear, actionable answer."
            )
            messages = [
                {"role": "system", "content": voice_prompt},
                {"role": "user", "content": text},
            ]
            response = llm.generate(messages)

        return {"response": response}

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
