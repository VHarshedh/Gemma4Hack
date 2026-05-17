"""
Aegis — Field Pipeline
======================
The main processing pipeline for Node A.
"""
import logging
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.models import FieldReport
from edge.backends import InferenceBackend
from edge.mesh import transmit_report

log = logging.getLogger("aegis.edge.pipeline")
console = Console()

def run_pipeline(
    audio_path: Path,
    image_path: Path,
    backend: InferenceBackend,
    *,
    transmit: bool = True,
) -> FieldReport:
    """Execute the full field ingestion pipeline."""
    console.print(
        Panel(
            f"[bold yellow]AEGIS Field Node - Pipeline Start[/]\n"
            f"Backend : {backend.name}\n"
            f"Audio   : {audio_path}\n"
            f"Image   : {image_path}",
            title="[NODE A] Field Operator",
            border_style="yellow",
        )
    )

    # Step 1: Audio Transcription
    # If ingestion wrote a fallback .txt (silence detected), use it directly.
    console.print("\n[bold]Step 1/3:[/] Transcribing audio …")
    fallback_txt = audio_path.with_suffix(".txt")
    if fallback_txt.exists():
        forced_transcript = fallback_txt.read_text(encoding="utf-8").strip()
        audio_result = {"transcript": forced_transcript, "duration_s": 0.0}
        console.print(Panel(
            f"[dim](silence fallback)[/]\n{forced_transcript}",
            title="[AUDIO] Transcript", border_style="blue",
        ))
    else:
        audio_result = backend.transcribe_audio(audio_path)
        console.print(Panel(audio_result["transcript"], title="[AUDIO] Transcript", border_style="blue"))

    # Step 2: Image Analysis
    console.print("\n[bold]Step 2/3:[/] Analysing image …")
    image_result = backend.analyze_image(image_path)
    console.print(Panel(image_result["analysis"], title="[VISUAL] Assessment", border_style="magenta"))

    # Step 3: Threat Classification
    console.print("\n[bold]Step 3/3:[/] Classifying threat …")
    classification = backend.classify_threat(
        audio_result["transcript"], image_result["analysis"]
    )

    # Build Report
    report = FieldReport(
        audio_transcript=audio_result["transcript"],
        image_analysis=image_result["analysis"],
        threat_level=classification.get("threat_level", image_result.get("threat_level", "high")),
        category=classification.get("category", image_result.get("category", "unknown")),
        confidence=classification.get("confidence", 0.0),
        raw_audio_duration_s=audio_result["duration_s"],
        model_backend=backend.name,
    )

    # Summary Table
    table = Table(title="[SUMMARY] Field Report", border_style="bold white")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Report ID", report.report_id)
    table.add_row("Threat Level", f"[bold red]{report.threat_level.upper()}[/]")
    table.add_row("Category", report.category)
    table.add_row("Confidence", f"{report.confidence:.0%}")
    table.add_row("Backend", report.model_backend)
    console.print(table)

    # Transmit
    if transmit:
        transmit_report(report)

    return report
