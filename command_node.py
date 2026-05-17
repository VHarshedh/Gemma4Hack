#!/usr/bin/env python3
"""
Aegis — Command Center (Node B): Entry Point
===========================================
Modularized entry point for the Aegis Command Node.
"""
import argparse
import logging
import uvicorn

from config import COMMAND_NODE_HOST, COMMAND_NODE_PORT, OLLAMA_MODEL
from server.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s|%(name)-16s|%(levelname)-7s|%(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("aegis.command")

def main():
    parser = argparse.ArgumentParser(
        description="Aegis Command Center — Node B",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Hardware guide (all models are Gemma 4):
  --ollama                  gemma4:e2b via Ollama  7.2 GB  CPU-friendly  ← recommended
  --ollama --ollama-model gemma4:27b               18 GB   GPU recommended
  --lite                    E2B GGUF direct        1.5 GB  CPU-only (no Ollama)
  --mock                    No model               0 GB    Demo / CI / Kaggle

Quickstart with Ollama:
  ollama pull gemma4:e2b
  python command_node.py --ollama
        """,
    )
    parser.add_argument("--mock",   action="store_true", help="Use mock LLM backend (no model needed)")
    parser.add_argument("--lite",   action="store_true", help="Use Gemma 4 E2B GGUF directly (~1.5 GB)")
    parser.add_argument("--ollama", action="store_true", help="Use Ollama backend (recommended for local use)")
    parser.add_argument("--ollama-model", default=OLLAMA_MODEL, metavar="MODEL",
                        help=f"Ollama model tag (default: {OLLAMA_MODEL})")
    parser.add_argument("--host", default=COMMAND_NODE_HOST)
    parser.add_argument("--port", type=int, default=COMMAND_NODE_PORT)
    args = parser.parse_args()

    app = create_app(use_mock=args.mock, use_lite=args.lite,
                     use_ollama=args.ollama, ollama_model=args.ollama_model)
    log.info("Starting Aegis Command Center on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")

if __name__ == "__main__":
    main()
