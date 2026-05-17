"""
Aegis — Field Ingestion
=======================
Capture live audio and image data from the local hardware.
Includes silence detection with randomised operator fallback,
and multi-index webcam probing with graceful mock fallback.
"""
import logging
import math
import secrets
import time
from pathlib import Path

import cv2
from rich.console import Console

from config import MOCK_IMAGE_PATH
from edge.cv import run_edge_detection

log = logging.getLogger("aegis.edge.ingestion")
console = Console()

# RMS amplitude below this level is treated as silence (range 0.0–1.0 for float32)
_SILENCE_RMS_THRESHOLD = 0.005

# Randomised operator radio calls used when no speech is detected
_FALLBACK_REPORTS = [
    "Command, this is Alpha-One. Structural collapse at Fourth and Harbor. Active gas leak, civilians trapped. Request HazMat and SAR. Over.",
    "Command, Bravo-Two. Flash flood on Coastal Highway, two vehicles submerged. Road is impassable. Requesting rescue boats and barrier teams. Over.",
    "This is Charlie-Three. Wildfire spreading east of the ridge line. Wind shift imminent. Evacuate Pacific Ridge sector immediately. Over.",
    "Delta-Four reporting. Chemical spill at Cascadia Chemical plant. Strong odour, workers evacuating. Need HazMat Level-A response. Over.",
    "Bravo-One to Command. Earthquake damage at the harbour. Pier collapse, multiple injuries. Requesting triage unit and structural engineers. Over.",
    "Alpha-Two here. Downed power lines on Oak Street after the tremor. Live wires on the road, traffic blocked. Need utilities crew and perimeter. Over.",
    "Command, Echo-Five. Tsunami warning active. Coastal zones below 10m are flooded. Evacuation route Delta is blocked. Use Route Alpha. Over.",
    "This is Foxtrot-Six. Residential building collapse on Third Avenue. Approximately 20 occupants unaccounted for. Request Urban SAR immediately. Over.",
]


def _rms(samples) -> float:
    """Return root-mean-square amplitude of a float32 audio array."""
    return math.sqrt(sum(float(x) ** 2 for x in samples.flatten()) / max(len(samples.flatten()), 1))


def capture_audio_live(duration_s: int = 10) -> Path:
    """
    Capture live audio from the default microphone.
    If the recording is silent (no speech detected), write a randomised
    operator radio call instead so the pipeline always has transcript input.
    """
    import sounddevice as sd
    import soundfile as sf

    console.print(f"\n[bold red]🎤 Recording audio for {duration_s} seconds... Speak now![/]")
    fs = 16000
    recording = sd.rec(int(duration_s * fs), samplerate=fs, channels=1, dtype='float32')
    sd.wait()

    out_path = Path("live_audio.wav")

    if _rms(recording) < _SILENCE_RMS_THRESHOLD:
        console.print(
            "[bold yellow]⚠️  No speech detected — injecting randomised operator report.[/]"
        )
        fallback_text = secrets.choice(_FALLBACK_REPORTS)
        console.print(f"[dim italic]Fallback: {fallback_text}[/]")
        # Write a silent WAV so the file is valid, and tag the path so the
        # MockBackend can detect it was a fallback (picked up via filename convention)
        sf.write(out_path, recording, fs)
        # Store chosen text alongside for the pipeline to pick up
        out_path.with_suffix(".txt").write_text(fallback_text, encoding="utf-8")
    else:
        console.print("[bold green]✅ Recording complete.[/]")
        sf.write(out_path, recording, fs)
        # Remove any stale fallback text file
        txt = out_path.with_suffix(".txt")
        if txt.exists():
            txt.unlink()

    return out_path


def capture_image_live() -> Path:
    """
    Capture a frame from the default webcam and run edge CV detection.
    Probes camera indices 0–3 before falling back to the mock image.
    """
    console.print("\n[bold yellow]📸 Capturing image from webcam...[/]")

    cap = None
    for idx in range(4):
        candidate = cv2.VideoCapture(idx)
        if candidate.isOpened():
            cap = candidate
            log.info("Webcam found at index %d", idx)
            break
        candidate.release()

    if cap is None:
        log.warning("No webcam found on indices 0-3. Falling back to mock image.")
        console.print("[bold yellow]⚠️  No webcam available — using mock image.[/]")
        return MOCK_IMAGE_PATH

    # Warm up — discard the first few frames
    for _ in range(5):
        cap.read()
        time.sleep(0.05)

    ret, frame = cap.read()
    cap.release()

    if not ret:
        log.warning("Webcam opened but failed to read a frame. Falling back to mock image.")
        console.print("[bold yellow]⚠️  Webcam read failed — using mock image.[/]")
        return MOCK_IMAGE_PATH

    frame, _ = run_edge_detection(frame)

    out_path = Path("live_image.jpg")
    cv2.imwrite(str(out_path), frame)
    console.print("[bold green]✅ Image captured.[/]")
    return out_path
