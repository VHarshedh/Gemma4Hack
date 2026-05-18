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

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import MOCK_AUDIO_PATH, MOCK_IMAGE_PATH, COMMAND_NODE_URL
from edge.backends import select_backend
from edge.ingestion import capture_audio_live, capture_image_live
from edge.mesh import sync_worker
from edge.pipeline import run_pipeline

# Safety checks per category.  Each entry is either:
#   str  — exact phrase required
#   list — OR group: any one synonym satisfies the check
# Any category not listed falls back to DEFAULT_CHECKS.
CATEGORY_SAFETY_CHECKS: dict[str, list] = {
    "chemical_spill": [
        "hazmat",
        ["mask", "respirator", "ppe", "scba", "protective", "breathing"],
    ],
    "gas_leak": [
        "hazmat",
        ["evacuate", "evacuation", "evacuating", "withdraw", "move", "clear"],
    ],
    "structural_collapse": [
        ["sar", "search and rescue", "rescue", "extraction", "extricate", "trapped"],
        ["evacuate", "evacuation", "move", "withdraw", "transport", "vector"],
    ],
    "wildfire": [
        ["evacuate", "evacuation", "withdraw", "move", "flee", "clear"],
        ["route", "road", "path", "corridor", "highway", "direction", "vector"],
    ],
    "tsunami": [
        ["evacuate", "evacuation", "withdraw", "move", "flee"],
        ["high ground", "higher ground", "elevation", "elevated", "inland", "uphill"],
    ],
    "earthquake": [
        ["evacuate", "evacuation", "withdraw", "move", "clear"],
    ],
    "mass_casualty": [
        ["triage", "medical", "casualty", "casualties", "ambulance", "ems", "trauma"],
    ],
    "flash_flood": [
        ["evacuate", "evacuation", "move", "withdraw", "transport", "vector", "clear"],
        ["route", "road", "path", "corridor", "highway", "direction", "vector"],
    ],
    "industrial_explosion": [
        "hazmat",
        ["evacuate", "evacuation", "withdraw", "move", "clear"],
    ],
    "nuclear_alert": [
        "hazmat",
        ["evacuate", "evacuation", "withdraw", "move", "clear"],
    ],
    "bridge_failure": [
        ["evacuate", "evacuation", "withdraw", "move", "clear"],
        ["route", "road", "path", "alternate", "direction", "vector"],
    ],
}
DEFAULT_CHECKS: list = [
    ["evacuate", "evacuation", "move", "withdraw", "clear", "vector", "transport"],
    ["route", "road", "path", "direction", "corridor", "vector"],
]


def _check_passes(checks: list, plan_lc: str) -> list[str]:
    """Return list of failed check labels (empty = all passed)."""
    failed = []
    for entry in checks:
        if isinstance(entry, list):
            if not any(s.lower() in plan_lc for s in entry):
                failed.append(f"any of {entry[:3]}…")
        else:
            if entry.lower() not in plan_lc:
                failed.append(entry)
    return failed


def poll_dispatch_plan(report_id: str, category: str, timeout: int = 1200) -> tuple[bool, list[str], str]:
    """
    Poll /api/v1/events until the dispatch plan for report_id is ready.
    Returns (passed, missing_keyword_labels, plan_text).
    """
    checks = CATEGORY_SAFETY_CHECKS.get(category, DEFAULT_CHECKS)
    url = f"{COMMAND_NODE_URL}/api/v1/events"
    console.print(f"  [dim]⏳ Waiting for dispatch plan (timeout {timeout}s) …[/]")

    with httpx.Client(timeout=15.0) as client:
        for elapsed in range(10, timeout + 1, 10):
            time.sleep(10)
            try:
                events = client.get(url).json().get("events", [])
                for ev in events:
                    if ev.get("report", {}).get("report_id") != report_id:
                        continue
                    status = ev.get("result", {}).get("status")
                    if status in ("processing", "synthesising", None):
                        console.print(f"  [dim]  [{elapsed}s] {status or 'processing'} …[/]")
                        break  # keep polling
                    plan = ev.get("result", {}).get("dispatch_plan", "") or ""
                    missing = _check_passes(checks, plan.lower())
                    return len(missing) == 0, missing, plan
            except Exception as exc:
                console.print(f"  [yellow]  poll error: {exc}[/]")

    return False, ["timeout"], ""  # timed out

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
        console.print("\n[bold magenta]Running 3 simulated mock scenarios sequentially…[/]")
        results: list[tuple[str, bool, list[str]]] = []

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

            if not args.no_transmit:
                passed, missing, _ = poll_dispatch_plan(report.report_id, report.category)
                if passed:
                    console.print(f"  [bold green]✅ PASSED — dispatch plan meets all safety constraints.[/]")
                else:
                    console.print(f"  [bold red]❌ FAILED — missing: {missing}[/]")
                results.append((report.category, passed, missing))

            if args.live:
                break

        # ── Final summary ───────────────────────────────────────────────
        if results:
            score = sum(1 for _, p, _ in results if p)
            colour = "green" if score == len(results) else "yellow" if score > 0 else "red"
            console.print()
            tbl = Table(title="[bold]Field Node Safety Summary[/]", border_style="bold white")
            tbl.add_column("Scenario", style="cyan")
            tbl.add_column("Category", style="white")
            tbl.add_column("Result", style="white")
            tbl.add_column("Missing Keywords", style="dim")
            for idx, (cat, passed, missing) in enumerate(results, 1):
                tbl.add_row(
                    f"#{idx}",
                    cat.replace("_", " ").title(),
                    "[green]✅ PASSED[/]" if passed else "[red]❌ FAILED[/]",
                    "—" if passed else ", ".join(missing),
                )
            console.print(tbl)
            console.print(f"\n[bold {colour}]Final Score: {score}/{len(results)} scenarios passed safety checks[/]\n")
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
