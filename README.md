# 🛡️ Aegis: Edge-Native Crisis Coordinator

Aegis is an advanced, edge-native, AI-driven crisis coordination platform built for rapid disaster response. Designed around the Gemma 4 model, Aegis bridges the gap between field operators in degraded network environments and centralized command centers.

## ✨ Key Features

### 📡 Edge-Native Architecture & Resilience
- **Encrypted Mesh Protocol**: SQLite-backed offline outbox with XOR-SHA256 encrypted payloads, integrity checksums, and automatic retry with back-off.
- **YOLOv8-nano Edge Vision**: Multi-class hazard detection (civilians, vehicles, animals) with disaster-context labels, falling back to HOG if ultralytics is unavailable.
- **Multimodal Ingestion**: Edge devices capture live audio and webcam imagery for rich situational awareness.
- **Peer Node Discovery**: Mesh outbox tracks peer field nodes for future P2P sync capability.

### 🧠 Multi-Agent Swarm Orchestration
- **Specialist Agents**: Three independent LLM agents (HazMat, Logistics, Medical) each analyse field reports with restricted tool access.
- **Commander Synthesis**: A Commander agent reconciles specialist assessments into a unified dispatch plan.
- **RAG-Enhanced Decisions**: SOPs, GIS data, and hazard info retrieved via PostGIS FTS and spatial queries.
- **Agentic Voice UI**: Commander voice queries are routed through an LLM-powered RAG pipeline that retrieves live GIS data + active events before generating context-aware responses.

### 🗺️ PostGIS Spatial Database
- **True Geospatial Queries**: Uses `ST_DWithin`, `ST_Distance_Sphere`, and GIST indices instead of Python-side Haversine.
- **Geometry Columns**: Hazards, safe zones, and sensor readings stored as PostGIS `GEOMETRY(Point, 4326)`.
- **SQLite Fallback**: Runs without Docker/PostGIS using the legacy SQLite backend.

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

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- `pip install -r requirements.txt`
- Docker & Docker Compose (optional, for PostGIS + MQTT)

### 1. Start Infrastructure (Optional — PostGIS + MQTT)
```bash
docker-compose up -d
python setup_postgis.py --reset
```

### 2. Initialize the SQLite Database (Fallback)
```bash
python setup_db.py --reset
```

### 3. Start the Command Node (Server)
```bash
python command_node.py --mock
```

### 4. Open the Dashboards
- **Commander Dashboard**: `http://localhost:8091`
- **Citizen Portal**: `http://localhost:8091/portal`

### 5. Run the Edge Node (Field Device)
```bash
python field_node.py --live --mock
```

### 6. Simulate Disaster & IoT Traffic
- **Disaster Simulator (30 concurrent reports):** `python simulate_chaos.py`
- **Smart City Sensors (MQTT):** `python sensor_network.py`
- **Smart City Sensors (HTTP fallback):** `python sensor_network.py --http`
- **LLM Safety Evaluator:** `python eval_safety.py`

## 🧪 Testing
```bash
pytest tests/ -v
```

## 🏗️ Architecture

```
Field Node (Gemma 4 E2B)  ──encrypted mesh──▶  Command Node (Gemma 4 31B)
  ├─ Audio Transcription                         ├─ Multi-Agent Swarm
  ├─ YOLOv8-nano Hazard Detection                │   ├─ HazMat Agent
  ├─ Threat Classification                       │   ├─ Logistics Agent
  └─ SQLite Encrypted Offline Outbox              │   ├─ Medical Agent
                                                  │   └─ Commander Agent
IoT Sensors ──MQTT──▶ Mosquitto                   ├─ Agentic Voice UI (RAG)
  ├─ Air Quality (AQI)                            ├─ PostGIS Spatial DB
  ├─ Seismic (Mw)                                 ├─ Sensor Threshold Alerts
  ├─ Flood (m)                                    └─ WebSocket Dashboard
  └─ Fire (°C)
```

