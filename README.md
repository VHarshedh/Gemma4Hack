# 🛡️ Aegis: Edge-Native Crisis Coordinator

Aegis is an advanced, edge-native, AI-driven crisis coordination platform built for rapid disaster response. Designed exclusively around **Gemma 4** models, Aegis bridges the gap between field operators in degraded network environments and centralised command centres.

---

## ✨ Key Features

### 📡 Edge-Native Architecture & Resilience
- **Encrypted Mesh Protocol**: SQLite-backed offline outbox with XOR-SHA256 encrypted payloads, integrity checksums, and automatic retry with back-off.
- **YOLOv8-nano Edge Vision**: Multi-class hazard detection (civilians, vehicles, animals) with disaster-context labels, falling back to HOG if ultralytics is unavailable.
- **Multimodal Ingestion**: Edge devices capture live audio and webcam imagery for rich situational awareness. Silence is auto-detected and replaced with a randomised operator radio call so the pipeline always has transcript input.
- **Peer Node Discovery**: Mesh outbox tracks peer field nodes for future P2P sync capability.

### 🧠 Multi-Agent Swarm Orchestration
- **Specialist Agents**: Three independent Gemma 4 agents (HazMat, Logistics, Medical) each analyse field reports with restricted tool access.
- **Commander Synthesis**: A Commander agent reconciles specialist assessments into a unified dispatch plan.
- **RAG-Enhanced Decisions**: SOPs, GIS data, and hazard info retrieved via PostGIS FTS and spatial queries.
- **Agentic Voice UI**: Commander voice queries are routed through an LLM-powered RAG pipeline that retrieves live GIS data and active events before generating context-aware responses.

### 🗺️ PostGIS Spatial Database
- **True Geospatial Queries**: Uses `ST_DWithin`, `ST_Distance_Sphere`, and GIST indices instead of Python-side Haversine.
- **Geometry Columns**: Hazards, safe zones, and sensor readings stored as PostGIS `GEOMETRY(Point, 4326)`.
- **SQLite Fallback**: Runs without Docker/PostGIS using the built-in SQLite backend — no setup required.

### 📡 Smart City IoT Sensor Network
- **MQTT-Based Telemetry**: Sensors publish to Mosquitto via MQTT topics (`aegis/sensors/#`).
- **Four Sensor Types**: Air Quality (AQI), Seismic (Mw), Flood (water-level), Fire (thermal).
- **Auto-Escalation**: Sensor readings exceeding thresholds automatically trigger hazard alerts on the dashboard.
- **HTTP Fallback**: Sensors can publish via REST API when MQTT is unavailable.

### 🗺️ Real-Time GIS & Operations Map
- **Zero-Latency WebSockets**: Push-based architecture for instant dashboard updates.
- **Predictive Heatmaps**: Dynamic `leaflet-heat` models for atmospheric hazard dispersion.
- **Autonomous Drone Fleets**: Animated drone dispatch to hazard coordinates.
- **Citizen Portal**: Public-facing evacuation portal with clear civilian directives.

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- `pip install -r requirements.txt`
- Docker & Docker Compose (optional — only needed for PostGIS + MQTT)

### 1. Seed the Database
```bash
python setup_db.py --reset
```

### 2. Start the Command Node

Choose the backend that matches your hardware (all use Gemma 4):

| Command | Model | RAM needed | Requires |
|---|---|---|---|
| `--mock` | None (deterministic) | 0 GB | Nothing |
| `--ollama` | gemma4:e2b via Ollama | ~8 GB RAM | Ollama installed |
| `--ollama --ollama-model gemma4:27b` | gemma4:27b via Ollama | ~20 GB RAM / 18 GB VRAM | Ollama + GPU |
| `--lite` | Gemma 4 E2B GGUF (raw) | ~1.5 GB RAM | GGUF file in `models/` |
| *(default)* | Gemma 4 27B GGUF (raw) | ~15 GB VRAM | GGUF file in `models/` |

**Recommended for most machines — Ollama:**
```bash
# Install Ollama: https://ollama.com/download
ollama pull gemma4:e2b        # 7.2 GB, resumable, CPU-friendly
python command_node.py --ollama
```

**Demo / CI / Kaggle — no model needed:**
```bash
python command_node.py --mock
```

**Low-spec laptop — raw GGUF, no Ollama:**
```bash
# Place gemma-4-E2B-it-Q4_K_M.gguf in models/
python command_node.py --lite
```

### 3. Open the Dashboards
- **Commander Dashboard**: `http://localhost:8091`
- **Citizen Portal**: `http://localhost:8091/portal`

### 4. Run the Field Node (Edge Device)

**Live mode** (requires microphone + webcam — laptop/PC only, not Kaggle):
```bash
python field_node.py --live
```
If no speech is detected during recording, a randomised operator radio call is injected automatically.

**Mock mode** (no hardware needed — works everywhere including Kaggle):
```bash
python field_node.py --mock
```

### 5. Start Optional Infrastructure (PostGIS + MQTT)
```bash
docker compose up -d gis-db mqtt-broker
python setup_postgis.py --reset
USE_POSTGIS=true python command_node.py --ollama
```

### 6. Simulate Disaster & IoT Traffic
```bash
python simulate_chaos.py          # 30 concurrent field reports
python sensor_network.py          # Smart city sensors via MQTT
python sensor_network.py --http   # Smart city sensors via HTTP fallback
python eval_safety.py             # LLM safety evaluator
```

---

## 🧪 Testing
```bash
pytest tests/ -v
```

Tests cover: FastAPI endpoints, GIS spatial queries, haversine, route queries, SOP full-text search, field report writes, `execute_tool` dispatcher, sensor thresholds, and WebSocket broadcasting. The test suite runs fully offline with SQLite — no Docker required.

---

## 🏗️ Architecture

```
Field Node (Gemma 4 E2B)  ──encrypted mesh──▶  Command Node (Gemma 4 via Ollama / GGUF)
  ├─ Live Audio + Silence Detection              ├─ Multi-Agent Swarm
  ├─ YOLOv8-nano Hazard Detection                │   ├─ HazMat Agent
  ├─ Threat Classification                       │   ├─ Logistics Agent
  └─ SQLite Encrypted Offline Outbox             │   ├─ Medical Agent
                                                 │   └─ Commander Agent
IoT Sensors ──MQTT──▶ Mosquitto                  ├─ Agentic Voice UI (RAG)
  ├─ Air Quality (AQI)                           ├─ PostGIS / SQLite GIS DB
  ├─ Seismic (Mw)                                ├─ Sensor Threshold Alerts
  ├─ Flood (m)                                   └─ WebSocket Dashboard
  └─ Fire (°C)
```

### LLM Backend Selection (all Gemma 4)

```
--mock    →  MockLLMBackend      (deterministic, 0 GB)
--ollama  →  OllamaBackend       (Ollama REST API, gemma4:e2b default)
--lite    →  LLMBackend          (llama-cpp-python, E2B GGUF ~1.5 GB)
default   →  LLMBackend          (llama-cpp-python, 27B GGUF ~15 GB)
```

---

## 📁 Project Structure

```
Aegis/
├── command_node.py          # Node B entry point
├── field_node.py            # Node A entry point
├── config.py                # All tunable parameters & model paths
├── core/
│   ├── llm.py               # LLMBackend, OllamaBackend, MockLLMBackend
│   ├── multi_agent.py       # MultiAgentEngine (HazMat / Logistics / Medical / Commander)
│   ├── gis_sqlite.py        # SQLite GIS backend
│   ├── gis_postgis.py       # PostGIS backend
│   ├── gis_tools.py         # Gemma 4 tool definitions (JSON schema)
│   └── models.py            # Pydantic data models
├── edge/
│   ├── backends.py          # LiteRT, Cactus, Mock inference backends
│   ├── ingestion.py         # Audio capture + silence detection, webcam capture
│   ├── cv.py                # YOLOv8-nano / HOG detection + TTS
│   ├── mesh.py              # Encrypted offline outbox + sync worker
│   └── pipeline.py          # Field ingestion pipeline
├── server/
│   ├── app.py               # FastAPI app factory
│   ├── mqtt.py              # MQTT listener (async, graceful retry)
│   ├── ws.py                # WebSocket connection manager
│   └── voice_utils.py       # Sensor thresholds + voice context builder
├── tests/
│   ├── conftest.py          # Session-scoped DB seed fixture
│   ├── test_api.py          # FastAPI endpoint tests
│   └── test_gis.py          # GIS database unit tests
├── data/                    # local_gis.db (SQLite)
├── models/                  # GGUF model weights (not committed)
├── templates/               # Jinja2 HTML (dashboard + citizen portal)
├── static/                  # CSS + JavaScript
├── setup_db.py              # SQLite database bootstrap
├── setup_postgis.py         # PostGIS database bootstrap
├── docker-compose.yml       # PostGIS + Mosquitto services
└── requirements.txt
```
