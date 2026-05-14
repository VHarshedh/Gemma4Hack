# 🛡️ Aegis: The Edge-Native Crisis Coordinator

> **Gemma 4 Good Hackathon — Global Resilience & Safety Tracks**

A decentralized, zero-internet disaster intelligence system that uses local **Gemma 4** models to route and verify field reports through a simulated mesh network.

## Architecture

```
┌──────────────────────────┐     Simulated Mesh     ┌──────────────────────────────┐
│  NODE A — Field Operator │     (Local REST API)    │  NODE B — Command Center     │
│                          │ ─────────────────────── │                              │
│  • Audio transcription   │    JSON Field Report    │  • Multi-turn reasoning      │
│  • Image hazard analysis │ ──────────────────────► │  • Function calling → SQLite │
│  • Threat classification │                         │  • Dispatch plan generation  │
│                          │    Dispatch Plan         │                              │
│  Model: Gemma 4 E2B      │ ◄─────────────────────  │  Model: Gemma 4 31B Dense    │
│  (2.3B params)           │                         │  (Q4_K_M quantised)          │
│  Runtime: LiteRT / Cactus│                         │  Runtime: llama-cpp-python   │
└──────────────────────────┘                         └──────────────────────────────┘
                                                              │
                                                     ┌────────┴────────┐
                                                     │  local_gis.db   │
                                                     │  (SQLite)       │
                                                     │  • Safe zones   │
                                                     │  • Hazards      │
                                                     │  • Routes       │
                                                     └─────────────────┘
```

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Download Model Weights
Place the model files in `./models/`:
- `gemma-4-E2B-it.litertlm` — Field Node (Node A)
- `gemma-4-31B-it-Q4_K_M.gguf` — Command Center (Node B)

### 3. Bootstrap the GIS Database
```bash
python setup_db.py
```

### 4. Start the Command Center (Terminal 1)
```bash
python command_node.py --mock    # Use --mock if no model weights yet
```

### 5. Run the Field Node (Terminal 2)
```bash
python field_node.py --mock      # Use --mock if no model weights yet
```

## Project Structure

```
Gemma4/
├── config.py           # Shared configuration (paths, params, endpoints)
├── setup_db.py         # Generates mock SQLite GIS database
├── field_node.py       # Node A — Edge multimodal ingestion
├── command_node.py     # Node B — Reasoning + tool calling server
├── requirements.txt    # Python dependencies
├── models/             # Place .gguf and .litertlm weights here
├── data/               # Generated local_gis.db lives here
├── mock_inputs/        # Sample audio/image files for testing
└── logs/               # Runtime logs
```

## Function Calling Schema

The Command Center defines three GIS tools as JSON schemas passed to Gemma 4 31B in the system prompt. The model uses `<think>` blocks for step-by-step reasoning, then emits `<tool_call>` tokens to invoke database queries:

| Tool | Purpose |
|------|---------|
| `query_safe_zones` | Find shelters/hospitals with remaining capacity near a GPS point |
| `query_hazards` | Retrieve known dangers (collapses, gas leaks, floods) in a radius |
| `query_routes` | Find evacuation routes with status and travel time estimates |

The multi-turn loop: **Think → Tool Call → Execute → Inject Result → Repeat → Dispatch Plan**.

## Sampling Parameters

Per Gemma 4 guidelines: `temperature=1.0`, `top_p=0.95`, `top_k=64`.

## License

Apache 2.0 — Aligned with Gemma 4's open-weight license.
# Gemma4Hack
