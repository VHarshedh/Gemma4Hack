#!/usr/bin/env python3
"""
Aegis — Field Node (Node A): Entry Point
========================================
Modularized entry point for the Aegis Field Node.
"""
import argparse
import json
import logging
import threading
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from config import MOCK_AUDIO_PATH, MOCK_IMAGE_PATH
from edge.backends import select_backend
from edge.ingestion import capture_audio_live, capture_image_live
from edge.mesh import sync_worker
from edge.pipeline import run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aegis.field")
console = Console()

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aegis Field Node — Multimodal edge ingestion & mesh transmission."
    )
    parser.add_argument("--audio", type=Path, default=MOCK_AUDIO_PATH)
    parser.add_argument("--image", type=Path, default=MOCK_IMAGE_PATH)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--no-transmit", action="store_true")
    parser.add_argument("--save-report", type=Path, default=None)
    args = parser.parse_args()

    console.print(
        Panel(
            "[bold green]AEGIS - The Edge-Native Crisis Coordinator[/]\n"
            "[dim]Field Node (Node A) - Modularized Architecture[/]",
            border_style="green",
        )
    )

    # Start background sync worker
    threading.Thread(target=sync_worker, daemon=True).start()

    backend = select_backend(force_mock=args.mock)

    if args.live:
        args.audio = capture_audio_live(duration_s=10)
        args.image = capture_image_live()

    if args.mock:
        console.print("\n[bold magenta]Running 3 simulated mock scenarios sequentially...[/]")
        for i in range(3):
            if i > 0:
                time.sleep(4)
            console.print(f"\n[bold green]=== Mock Scenario {i+1} of 3 ===[/]")
            report = run_pipeline(
                audio_path=args.audio,
                image_path=args.image,
                backend=backend,
                transmit=not args.no_transmit,
            )
            if args.live: break
    else:
        report = run_pipeline(
            audio_path=args.audio,
            image_path=args.image,
            backend=backend,
            transmit=not args.no_transmit,
        )

    if args.save_report:
        args.save_report.parent.mkdir(parents=True, exist_ok=True)
        args.save_report.write_text(json.dumps(report.model_dump(), indent=2))

if __name__ == "__main__":
    main()
