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
# Database
# ──────────────────────────────────────────────────────────────────────
DATABASE_PATH = PROJECT_ROOT / "data" / "local_gis.db"

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
