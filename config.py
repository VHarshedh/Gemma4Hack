"""
Aegis: Edge-Native Crisis Coordinator
======================================
Shared configuration module for all Aegis nodes.

This module centralises every tunable parameter—model paths, network
endpoints, sampling hyper-parameters, and database locations—so that
both the Field Node (Node A) and the Command Center (Node B) share a
single source of truth.

All paths default to project-relative locations so the system works
out-of-the-box after cloning the repo and dropping the model weights
into ./models/.
"""

import os
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────
# Project Root
# ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent

# ──────────────────────────────────────────────────────────────────────
# Model Weights  (users must download these separately)
# ──────────────────────────────────────────────────────────────────────
# Node A – Gemma 4 E2B (2.3B effective params, multimodal edge model)
#   Supports native audio transcription + image understanding.
#   Expected format: LiteRT LM package (.litertlm)
FIELD_MODEL_PATH = PROJECT_ROOT / "models" / "gemma-4-E2B-it.litertlm"

# Node B – Gemma 4 31B Dense (quantised GGUF for llama.cpp)
#   Full-power model for reasoning, tool use, and dispatch planning.
COMMAND_MODEL_PATH = PROJECT_ROOT / "models" / "gemma-4-31B-it-Q4_K_M.gguf"

# ──────────────────────────────────────────────────────────────────────
# Gemma 4 Sampling Parameters  (official recommended defaults)
# ──────────────────────────────────────────────────────────────────────
TEMPERATURE = 1.0
TOP_P = 0.95
TOP_K = 64
MAX_TOKENS = 2048          # Max generation length per request
CONTEXT_SIZE = 8192        # Context window for llama-cpp-python

# ──────────────────────────────────────────────────────────────────────
# Network – Simulated Mesh Link (local REST API)
# ──────────────────────────────────────────────────────────────────────
COMMAND_NODE_HOST = "127.0.0.1"
COMMAND_NODE_PORT = 8091
COMMAND_NODE_URL = f"http://{COMMAND_NODE_HOST}:{COMMAND_NODE_PORT}"

# ──────────────────────────────────────────────────────────────────────
# Database – SQLite fallback (legacy, used when PostGIS unavailable)
# ──────────────────────────────────────────────────────────────────────
DATABASE_PATH = PROJECT_ROOT / "data" / "local_gis.db"

# ──────────────────────────────────────────────────────────────────────
# Database – PostgreSQL + PostGIS (Phase 1 upgrade)
# ──────────────────────────────────────────────────────────────────────
# Set USE_POSTGIS=true to enable the PostGIS backend.  When disabled
# the system falls back to the original SQLite GIS implementation.
USE_POSTGIS = os.getenv("USE_POSTGIS", "false").lower() in ("true", "1", "yes")

PG_HOST = os.getenv("DB_HOST", "127.0.0.1")
PG_PORT = int(os.getenv("DB_PORT", "5432"))
PG_USER = os.getenv("DB_USER", "aegis")
PG_PASS = os.getenv("DB_PASS", "aegis_secure")
PG_NAME = os.getenv("DB_NAME", "aegis_gis")
PG_DSN  = f"host={PG_HOST} port={PG_PORT} dbname={PG_NAME} user={PG_USER} password={PG_PASS}"

# ──────────────────────────────────────────────────────────────────────
# MQTT – IoT Sensor Network Broker (Phase 1 upgrade)
# ──────────────────────────────────────────────────────────────────────
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC_SENSORS = "aegis/sensors/#"          # Wildcard subscription
MQTT_TOPIC_AQI     = "aegis/sensors/air_quality"
MQTT_TOPIC_SEISMIC = "aegis/sensors/seismic"
MQTT_TOPIC_FLOOD   = "aegis/sensors/flood"
MQTT_TOPIC_FIRE    = "aegis/sensors/fire"

# ──────────────────────────────────────────────────────────────────────
# Multi-Agent Orchestration (Phase 1 upgrade)
# ──────────────────────────────────────────────────────────────────────
# Each specialist agent has its own system prompt and tool subset.
AGENT_MAX_DEBATE_ROUNDS = 2   # rounds of inter-agent debate
AGENT_SPECIALISTS = ["hazmat", "logistics", "medical"]

# ──────────────────────────────────────────────────────────────────────
# Mock Inputs  (for demo / testing without real sensor data)
# ──────────────────────────────────────────────────────────────────────
MOCK_AUDIO_PATH = PROJECT_ROOT / "mock_inputs" / "sample_report.wav"
MOCK_IMAGE_PATH = PROJECT_ROOT / "mock_inputs" / "sample_hazard.jpg"

# ──────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────
LOG_DIR = PROJECT_ROOT / "logs"
LOG_LEVEL = "INFO"
