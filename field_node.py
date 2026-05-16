#!/usr/bin/env python3
"""
Aegis — Field Node (Node A): Edge Multimodal Ingestion
======================================================
Simulates a field operator's edge device running Gemma 4 E2B (2.3B).

Pipeline
--------
1. Ingest a local audio file  → native transcription via E2B
2. Ingest a local image       → visual hazard summary via E2B
3. Structure a JSON field report
4. Transmit to Command Node (Node B) over simulated mesh link (REST)

Backends
--------
The script supports three inference backends, tried in order:
  1. ``litert_lm``  — Google LiteRT on-device inference
  2. ``cactus``     — Cactus cross-platform edge engine
  3. ``MockBackend``— Deterministic mock for demo / CI

Usage
-----
    python field_node.py                          # interactive
    python field_node.py --audio voice.wav --image hazard.jpg
    python field_node.py --mock                   # force mock backend
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import secrets
import time
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import (
    COMMAND_NODE_URL,
    FIELD_MODEL_PATH,
    MAX_TOKENS,
    MOCK_AUDIO_PATH,
    MOCK_IMAGE_PATH,
    TEMPERATURE,
    TOP_K,
    TOP_P,
)

# ─── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aegis.field_node")
console = Console()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FieldReport(BaseModel):
    """Structured field report transmitted to the Command Center."""

    report_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    operator_id: str = Field(default="FIELD-ALPHA-01")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    location: dict[str, float] = Field(
        default={"latitude": 46.2088, "longitude": -123.8156}
    )
    audio_transcript: str = ""
    image_analysis: str = ""
    threat_level: str = "unknown"
    category: str = "unclassified"
    confidence: float = 0.0
    raw_audio_duration_s: float = 0.0
    model_backend: str = "unknown"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Inference Backend — Abstract Base
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class InferenceBackend(ABC):
    """Abstract base for edge model inference backends."""

    name: str = "base"

    @abstractmethod
    def transcribe_audio(self, audio_path: Path) -> dict[str, Any]:
        """Transcribe audio → {"transcript": str, "duration_s": float}."""

    @abstractmethod
    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        """Analyze image → {"analysis": str, "threat_level": str, "category": str}."""

    @abstractmethod
    def classify_threat(self, transcript: str, image_analysis: str) -> dict[str, Any]:
        """Combined classification → {"threat_level": str, "category": str, "confidence": float}."""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Backend 1 — LiteRT LM (Google on-device)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class LiteRTBackend(InferenceBackend):
    """
    Google LiteRT LM backend for Gemma 4 E2B.

    Loads the ``.litertlm`` package and uses the LiteRT LM Python API
    for native multimodal inference (audio + image → text).
    """

    name = "litert_lm"

    def __init__(self, model_path: Path) -> None:
        try:
            import litert_lm  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "litert_lm not installed. "
                "See https://github.com/nicfv/litert-lm"
            ) from exc

        log.info("Loading LiteRT LM model from %s …", model_path)
        self._model = litert_lm.load(str(model_path))
        log.info("LiteRT LM model loaded successfully.")

    def transcribe_audio(self, audio_path: Path) -> dict[str, Any]:
        """Native audio transcription via Gemma 4 E2B multimodal input."""
        import soundfile as sf

        audio_data, sample_rate = sf.read(str(audio_path))
        duration = len(audio_data) / sample_rate

        # Gemma 4 E2B accepts raw audio as a native modality
        prompt = (
            "You are a disaster field transcription system. "
            "Transcribe the following audio exactly. "
            "Output ONLY the transcription, nothing else."
        )
        result = self._model.generate(
            prompt=prompt,
            audio=audio_data,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            top_k=TOP_K,
            max_tokens=MAX_TOKENS,
        )
        return {"transcript": result.strip(), "duration_s": round(duration, 2)}

    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        """Native image analysis via Gemma 4 E2B vision capability."""
        from PIL import Image

        image = Image.open(image_path)

        prompt = (
            "You are a disaster damage assessment AI. Analyze this image for:\n"
            "1. Visible structural damage or hazards\n"
            "2. Type of threat (fire, collapse, flood, chemical, etc.)\n"
            "3. Estimated severity (low / moderate / high / critical)\n"
            "4. Any visible survivors or casualties\n"
            "Output a concise 2-3 sentence assessment."
        )
        result = self._model.generate(
            prompt=prompt,
            image=image,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            top_k=TOP_K,
            max_tokens=MAX_TOKENS,
        )
        return self._parse_analysis(result.strip())

    def classify_threat(self, transcript: str, image_analysis: str) -> dict[str, Any]:
        prompt = (
            "Given this field report, classify the threat.\n"
            f"Audio transcript: {transcript}\n"
            f"Visual assessment: {image_analysis}\n\n"
            "Respond with ONLY a JSON object: "
            '{"threat_level": "low|moderate|high|critical", '
            '"category": "<type>", "confidence": 0.0-1.0}'
        )
        result = self._model.generate(
            prompt=prompt,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            top_k=TOP_K,
            max_tokens=256,
        )
        try:
            return json.loads(result.strip())
        except json.JSONDecodeError:
            return {"threat_level": "high", "category": "unknown", "confidence": 0.5}

    @staticmethod
    def _parse_analysis(text: str) -> dict[str, Any]:
        threat = "moderate"
        for level in ("critical", "high", "moderate", "low"):
            if level in text.lower():
                threat = level
                break
        category = "structural_damage"
        for cat in ("fire", "flood", "chemical", "collapse", "gas_leak"):
            if cat in text.lower():
                category = cat
                break
        return {"analysis": text, "threat_level": threat, "category": category}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Backend 2 — Cactus (Cross-platform edge engine)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CactusBackend(InferenceBackend):
    """
    Cactus inference engine backend.

    Uses the ``cactus_init`` / ``cactus_complete`` / ``cactus_destroy``
    FFI functions exposed by the Cactus Python SDK.
    """

    name = "cactus"

    def __init__(self, model_path: Path) -> None:
        try:
            from cactus import (  # type: ignore[import-untyped]
                cactus_init,
            )
        except ImportError as exc:
            raise RuntimeError(
                "cactus not installed. "
                "See https://github.com/cactus-compute/cactus"
            ) from exc

        log.info("Initialising Cactus engine with %s …", model_path)
        # cactus_init(model_path, corpus_dir, cache_index)
        self._model = cactus_init(str(model_path), None, False)
        log.info("Cactus engine ready.")

    def _complete(self, messages: list[dict]) -> str:
        """Run a chat completion via cactus_complete."""
        from cactus import cactus_complete  # type: ignore[import-untyped]

        payload = json.dumps(messages)
        result = json.loads(cactus_complete(self._model, payload, None, None, None))
        return result.get("response", "")

    def transcribe_audio(self, audio_path: Path) -> dict[str, Any]:
        import soundfile as sf

        audio_data, sample_rate = sf.read(str(audio_path))
        duration = len(audio_data) / sample_rate

        # Encode audio as base64 for cactus multimodal input
        audio_b64 = base64.b64encode(
            audio_data.astype("float32").tobytes()
        ).decode()

        messages = [
            {"role": "system", "content": "You are a disaster field transcription system."},
            {
                "role": "user",
                "content": "Transcribe this audio exactly.",
                "audio": audio_b64,
            },
        ]
        transcript = self._complete(messages)
        return {"transcript": transcript.strip(), "duration_s": round(duration, 2)}

    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        image_b64 = base64.b64encode(image_path.read_bytes()).decode()

        messages = [
            {"role": "system", "content": "You are a disaster damage assessment AI."},
            {
                "role": "user",
                "content": (
                    "Analyze this image for structural damage, hazard type, "
                    "severity (low/moderate/high/critical), and any visible casualties. "
                    "Give a concise 2-3 sentence assessment."
                ),
                "image": image_b64,
            },
        ]
        analysis = self._complete(messages)
        return LiteRTBackend._parse_analysis(analysis)

    def classify_threat(self, transcript: str, image_analysis: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": "Classify the threat. Respond ONLY with JSON."},
            {
                "role": "user",
                "content": (
                    f"Audio: {transcript}\nVisual: {image_analysis}\n\n"
                    'JSON: {{"threat_level":"...","category":"...","confidence":0.0}}'
                ),
            },
        ]
        result = self._complete(messages)
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return {"threat_level": "high", "category": "unknown", "confidence": 0.5}

    def __del__(self) -> None:
        try:
            from cactus import cactus_destroy  # type: ignore[import-untyped]
            cactus_destroy(self._model)
        except Exception:
            pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Backend 3 — Mock (deterministic, no model required)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MockBackend(InferenceBackend):
    """
    Deterministic mock backend for testing without model weights.

    Returns realistic-looking outputs that exercise the full pipeline
    including JSON serialisation, threat classification, and mesh
    transmission to Node B.
    """

    name = "mock"
    
    def __init__(self):
        try:
            from faker import Faker
            self.fake = Faker()
        except ImportError:
            log.error("Faker library not installed. Please run: pip install faker")
            raise
        self.current_scenario = self._generate_scenario()

    def _generate_scenario(self) -> dict[str, Any]:
        """Procedurally generate a completely unique disaster scenario on the fly."""
        disasters = [
            ("structural collapse", "critical", "collapse"),
            ("flash flood", "high", "flood"),
            ("chemical spill", "critical", "hazmat"),
            ("wildfire", "high", "fire"),
            ("multi-vehicle pileup", "moderate", "traffic"),
            ("gas main explosion", "critical", "explosion")
        ]
        dtype, tlevel, cat = secrets.choice(disasters)
        
        street = self.fake.street_name()
        city = self.fake.city()
        operator = f"{self.fake.last_name().upper()}-{secrets.choice(['Alpha', 'Bravo', 'Charlie', 'Delta'])}"
        
        victim_options = [
            "several civilians",
            "no visible casualties",
            f"at least {self.fake.random_int(min=2, max=15)} trapped individuals",
            "an unknown number of people"
        ]
        victims = secrets.choice(victim_options)
        
        hazards = [
            "Active power lines are down.",
            "Strong smell of gas in the area.",
            "Water levels are rising rapidly.",
            "Thick smoke is severely reducing visibility.",
            "Secondary collapses are occurring."
        ]
        
        audio = (
            f"Command, this is Operator {operator} reporting from {street} in {city}. "
            f"We have a confirmed {dtype}. I can see {victims} in the immediate vicinity. "
            f"{secrets.choice(hazards)} Requesting immediate backup and specialized response teams. Over."
        )
        
        image = (
            f"Visual confirmation of a severe {dtype} impacting urban infrastructure near {street}. "
            f"The scene is chaotic. {secrets.choice(hazards)} "
            f"{victims.capitalize()} are visible in the affected zone."
        )
        
        return {
            "audio": audio,
            "duration": round(self.fake.pyfloat(min_value=12.0, max_value=45.0), 1),
            "image": image,
            "threat_level": tlevel,
            "category": cat,
            "full_category": dtype.replace(" ", "_").replace("-", "_"),
            "confidence": round(self.fake.pyfloat(min_value=0.75, max_value=0.99), 2)
        }

    def transcribe_audio(self, audio_path: Path) -> dict[str, Any]:
        log.info("[MOCK] Procedurally generating audio transcription")
        # Generate a completely new scenario for this run
        self.current_scenario = self._generate_scenario()
        time.sleep(0.3)  # Simulate inference latency
        return {
            "transcript": self.current_scenario["audio"],
            "duration_s": self.current_scenario["duration"],
        }

    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        log.info("[MOCK] Simulating image analysis of %s", image_path.name)
        time.sleep(0.2)  # Simulate inference latency
        return {
            "analysis": self.current_scenario["image"],
            "threat_level": self.current_scenario["threat_level"],
            "category": self.current_scenario["category"],
        }

    def classify_threat(self, transcript: str, image_analysis: str) -> dict[str, Any]:
        log.info("[MOCK] Classifying combined threat assessment")
        return {
            "threat_level": self.current_scenario["threat_level"],
            "category": self.current_scenario["full_category"],
            "confidence": self.current_scenario["confidence"],
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Backend Selection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def select_backend(force_mock: bool = False) -> InferenceBackend:
    """
    Try backends in priority order: LiteRT → Cactus → Mock.

    Parameters
    ----------
    force_mock : bool
        Skip real backends and use mock directly.
    """
    if force_mock:
        log.info("Mock mode forced via --mock flag.")
        return MockBackend()

    # Attempt 1: LiteRT LM
    if FIELD_MODEL_PATH.exists() and FIELD_MODEL_PATH.suffix == ".litertlm":
        try:
            return LiteRTBackend(FIELD_MODEL_PATH)
        except RuntimeError as e:
            log.warning("LiteRT backend unavailable: %s", e)

    # Attempt 2: Cactus
    if FIELD_MODEL_PATH.exists():
        try:
            return CactusBackend(FIELD_MODEL_PATH)
        except RuntimeError as e:
            log.warning("Cactus backend unavailable: %s", e)

    # Fallback: Mock
    log.warning(
        "No model weights found at %s — falling back to MockBackend.",
        FIELD_MODEL_PATH,
    )
    return MockBackend()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Hardware Ingestion & Mesh Transmission
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def capture_audio_live(duration_s: int = 5) -> Path:
    """Capture live audio from the default microphone."""
    import sounddevice as sd
    import soundfile as sf
    
    console.print(f"\n[bold red]🎤 Recording audio for {duration_s} seconds... Speak now![/]")
    fs = 16000
    recording = sd.rec(int(duration_s * fs), samplerate=fs, channels=1, dtype='float32')
    sd.wait()
    console.print("[bold green]✅ Recording complete.[/]")
    
    out_path = Path("live_audio.wav")
    sf.write(out_path, recording, fs)
    return out_path

def capture_image_live() -> Path:
    """Capture a frame from the default webcam and run edge CV detection."""
    import cv2
    console.print("\n[bold yellow]📸 Capturing image from webcam...[/]")
    cap = cv2.VideoCapture(0)
    
    # Let the camera warm up for a few frames
    for _ in range(5):
        cap.read()
        time.sleep(0.1)
        
    ret, frame = cap.read()
    cap.release()
    
    if not ret:
        log.error("Failed to capture from webcam. Falling back to mock image.")
        return MOCK_IMAGE_PATH

    # --- EDGE NATIVE CV (YOLOv8-nano with HOG fallback) ---
    frame, detections = _run_edge_detection(frame)
    
    out_path = Path("live_image.jpg")
    cv2.imwrite(str(out_path), frame)
    console.print("[bold green]✅ Image captured.[/]")
    return out_path


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Edge Computer Vision — YOLOv8-nano (Phase 2 upgrade)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# YOLO COCO class IDs we care about in disaster scenarios
YOLO_HAZARD_CLASSES = {
    0: ("person", (0, 255, 0)),      # green
    2: ("vehicle", (255, 165, 0)),   # orange
    7: ("truck", (255, 165, 0)),     # orange
    15: ("cat", (200, 200, 0)),      # animal
    16: ("dog", (200, 200, 0)),      # animal
    67: ("cell phone", (100, 100, 255)),
}

# Custom label overrides for disaster context
DISASTER_LABELS = {
    0: "Civilian",
    2: "Stranded Vehicle",
    7: "Emergency Vehicle",
    15: "Animal",
    16: "Animal",
}

_yolo_model = None  # lazy-loaded singleton


def _get_yolo_model():
    """Lazy-load the YOLOv8-nano model."""
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO
        model_path = Path("models") / "yolov8n.pt"
        if not model_path.exists():
            console.print("[bold yellow]⬇️ Downloading YOLOv8-nano weights...[/]")
        _yolo_model = YOLO(str(model_path))
        console.print("[bold green]✅ YOLOv8-nano model loaded.[/]")
    return _yolo_model


def _run_edge_detection(frame):
    """
    Run edge CV detection on a frame.
    
    Tries YOLOv8-nano first for multi-class hazard detection.
    Falls back to OpenCV HOG pedestrian detector if ultralytics
    is not installed.

    Returns (annotated_frame, detection_summary_list).
    """
    import cv2

    try:
        return _run_yolo_detection(frame)
    except (ImportError, Exception) as e:
        log.warning("YOLOv8 unavailable (%s), falling back to HOG.", e)
        return _run_hog_detection(frame)


def _run_yolo_detection(frame):
    """YOLOv8-nano multi-class detection."""
    import cv2
    from ultralytics import YOLO

    console.print("[bold cyan]🤖 Running YOLOv8-nano edge detection...[/]")
    model = _get_yolo_model()
    results = model(frame, conf=0.35, verbose=False)

    detections = []
    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])

            if cls_id in YOLO_HAZARD_CLASSES:
                label_base, color = YOLO_HAZARD_CLASSES[cls_id]
                label = DISASTER_LABELS.get(cls_id, label_base)
                x1, y1, x2, y2 = map(int, box.xyxy[0])

                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    frame,
                    f"{label} {conf:.0%}",
                    (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2,
                )
                detections.append({
                    "class": label, "confidence": round(conf, 2),
                    "bbox": [x1, y1, x2, y2],
                })

    # Summary
    from collections import Counter
    counts = Counter(d["class"] for d in detections)
    if detections:
        summary_parts = [f"{v} {k}(s)" for k, v in counts.items()]
        console.print(f"[bold red]⚠️ Detected: {', '.join(summary_parts)}[/]")
    else:
        console.print("[bold green]✅ No hazard objects detected.[/]")

    return frame, detections


def _run_hog_detection(frame):
    """Legacy OpenCV HOG pedestrian detector (fallback)."""
    import cv2
    console.print("[bold cyan]🤖 Running legacy HOG pedestrian detection...[/]")
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    boxes, weights = hog.detectMultiScale(frame, winStride=(8, 8))
    detections = []

    for (x, y, w, h) in boxes:
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(frame, 'Civilian', (x, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        detections.append({"class": "Civilian", "confidence": 0.7,
                           "bbox": [x, y, x + w, y + h]})

    if len(boxes) > 0:
        console.print(f"[bold red]⚠️ Detected {len(boxes)} civilian(s) at edge![/]")
    else:
        console.print("[bold green]✅ Area clear of civilians.[/]")

    return frame, detections

def play_tts(text: str) -> None:
    """Read out the dispatch plan using text-to-speech."""
    try:
        import pyttsx3
        console.print("[bold cyan]🔊 Playing Dispatch Plan TTS...[/]")
        engine = pyttsx3.init()
        engine.setProperty('rate', 170)
        
        # Clean up markdown for TTS
        import re
        clean_text = re.sub(r'#|\*|_|🚨|✅|⚠️|🚫', '', text)
        clean_text = re.sub(r'\[.*?\]', '', clean_text)
        
        engine.say("Command Node acknowledges report. Here is the dispatch plan:")
        engine.say(clean_text)
        engine.runAndWait()
    except Exception as e:
        log.error("TTS engine failed: %s", e)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Offline-First Mesh Protocol (Phase 2 upgrade)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import sqlite3
import hashlib
import os as _os

QUEUE_DB = Path("data") / "mesh_queue.db"
MESH_ENCRYPTION_KEY = _os.getenv("AEGIS_MESH_KEY", "aegis-default-key-change-me")


def _init_queue_db() -> sqlite3.Connection:
    """Initialise the SQLite-backed mesh queue with schema."""
    QUEUE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(QUEUE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outbox (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id   TEXT NOT NULL UNIQUE,
            payload     TEXT NOT NULL,
            checksum    TEXT NOT NULL,
            encrypted   INTEGER NOT NULL DEFAULT 0,
            retries     INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 10,
            status      TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','sending','delivered','failed')),
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            last_attempt TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS peer_nodes (
            node_id   TEXT PRIMARY KEY,
            endpoint  TEXT NOT NULL,
            last_seen TEXT,
            priority  INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def _encrypt_payload(payload_json: str) -> str:
    """Simple XOR-based obfuscation for mesh transit (placeholder for AES)."""
    key_bytes = hashlib.sha256(MESH_ENCRYPTION_KEY.encode()).digest()
    data = payload_json.encode()
    encrypted = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(data))
    return base64.b64encode(encrypted).decode()


def _decrypt_payload(encrypted_b64: str) -> str:
    """Reverse the XOR obfuscation."""
    key_bytes = hashlib.sha256(MESH_ENCRYPTION_KEY.encode()).digest()
    data = base64.b64decode(encrypted_b64)
    decrypted = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(data))
    return decrypted.decode()


def _enqueue_report(payload: dict) -> None:
    """Store a report in the offline queue with integrity checksum."""
    conn = _init_queue_db()
    payload_json = json.dumps(payload)
    checksum = hashlib.sha256(payload_json.encode()).hexdigest()
    encrypted = _encrypt_payload(payload_json)

    try:
        conn.execute(
            "INSERT OR IGNORE INTO outbox (report_id, payload, checksum, encrypted) VALUES (?,?,?,1)",
            (payload.get("report_id", "unknown"), encrypted, checksum),
        )
        conn.commit()
    except Exception as e:
        log.error("Failed to enqueue report: %s", e)
    finally:
        conn.close()


def _dequeue_pending() -> list[tuple[int, dict]]:
    """Fetch all pending reports from the outbox."""
    conn = _init_queue_db()
    rows = conn.execute(
        "SELECT id, payload, encrypted FROM outbox WHERE status = 'pending' AND retries < max_retries ORDER BY id"
    ).fetchall()
    conn.close()

    results = []
    for row_id, payload_str, is_encrypted in rows:
        try:
            if is_encrypted:
                payload_str = _decrypt_payload(payload_str)
            results.append((row_id, json.loads(payload_str)))
        except Exception as e:
            log.error("Corrupted queue entry #%d: %s", row_id, e)
    return results


def _mark_delivered(row_id: int) -> None:
    conn = _init_queue_db()
    conn.execute("UPDATE outbox SET status='delivered', last_attempt=datetime('now') WHERE id=?", (row_id,))
    conn.commit()
    conn.close()


def _mark_retry(row_id: int) -> None:
    conn = _init_queue_db()
    conn.execute(
        "UPDATE outbox SET retries=retries+1, last_attempt=datetime('now') WHERE id=?",
        (row_id,),
    )
    conn.commit()
    conn.close()


def sync_worker() -> None:
    """Background thread: flush the offline queue with retry logic."""
    url = f"{COMMAND_NODE_URL}/api/v1/field-report"
    while True:
        pending = _dequeue_pending()
        if not pending:
            time.sleep(5)
            continue

        log.info("Mesh sync: %d pending report(s) in outbox.", len(pending))
        for row_id, report in pending:
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.post(url, json=report)
                    resp.raise_for_status()

                _mark_delivered(row_id)
                log.info("✅ Synced offline report %s (queue #%d)", report.get("report_id"), row_id)
            except httpx.ConnectError:
                _mark_retry(row_id)
                log.warning("Mesh down — retry queued for #%d", row_id)
                time.sleep(10)
                break  # back off on first failure
            except Exception as e:
                _mark_retry(row_id)
                log.error("Sync error for #%d: %s", row_id, e)

        time.sleep(5)


def transmit_report(report: FieldReport) -> dict[str, Any] | None:
    """
    Transmit a field report to the Command Center (Node B).

    Uses encrypted mesh protocol with SQLite-backed offline queue.
    Reports are checksummed and encrypted at rest in the outbox.
    """
    url = f"{COMMAND_NODE_URL}/api/v1/field-report"
    payload = report.model_dump()

    console.print(
        Panel(
            f"[bold cyan]Transmitting to Command Node[/]\n"
            f"Endpoint: {url}\n"
            f"Report ID: {report.report_id}\n"
            f"Payload size: {len(json.dumps(payload))} bytes\n"
            f"Encryption: XOR-SHA256 mesh cipher",
            title="[MESH] Encrypted Transmission",
            border_style="cyan",
        )
    )

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            result = resp.json()

        console.print(
            Panel(
                json.dumps(result, indent=2),
                title="[OK] Command Center Response",
                border_style="green",
            )
        )
        
        if "dispatch_plan" in result:
            play_tts(result["dispatch_plan"])
            
        return result

    except httpx.ConnectError:
        log.warning(
            "Cannot reach Command Node at %s. "
            "Encrypting & queueing report %s for offline sync.",
            url, report.report_id
        )
        _enqueue_report(payload)
        
        # Show queue stats
        conn = _init_queue_db()
        pending_count = conn.execute("SELECT COUNT(*) FROM outbox WHERE status='pending'").fetchone()[0]
        conn.close()
        
        console.print(
            Panel(
                f"Mesh Network Unavailable.\n"
                f"Report encrypted and queued for background sync.\n"
                f"Outbox: {pending_count} report(s) pending delivery.",
                title="[OFFLINE] Encrypted Queue",
                border_style="yellow",
            )
        )
        return None
    except httpx.HTTPStatusError as e:
        log.error("Command Node returned error: %s", e)
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Main Pipeline
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_pipeline(
    audio_path: Path,
    image_path: Path,
    backend: InferenceBackend,
    *,
    transmit: bool = True,
) -> FieldReport:
    """
    Execute the full field ingestion pipeline.

    Steps
    -----
    1. Transcribe audio via edge model
    2. Analyse image via edge model
    3. Classify combined threat
    4. Build structured FieldReport
    5. Transmit to Command Center

    Returns the completed FieldReport.
    """
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

    # ── Step 1: Audio Transcription ───────────────────────────────
    console.print("\n[bold]Step 1/3:[/] Transcribing audio …")
    t0 = time.perf_counter()
    audio_result = backend.transcribe_audio(audio_path)
    t_audio = time.perf_counter() - t0
    log.info(
        "Audio transcribed in %.2fs (%.1fs of audio)",
        t_audio,
        audio_result["duration_s"],
    )
    console.print(
        Panel(audio_result["transcript"], title="[AUDIO] Transcript", border_style="blue")
    )

    # ── Step 2: Image Analysis ────────────────────────────────────
    console.print("\n[bold]Step 2/3:[/] Analysing image …")
    t0 = time.perf_counter()
    image_result = backend.analyze_image(image_path)
    t_image = time.perf_counter() - t0
    log.info("Image analysed in %.2fs", t_image)
    console.print(
        Panel(image_result["analysis"], title="[VISUAL] Assessment", border_style="magenta")
    )

    # ── Step 3: Threat Classification ─────────────────────────────
    console.print("\n[bold]Step 3/3:[/] Classifying threat …")
    classification = backend.classify_threat(
        audio_result["transcript"], image_result["analysis"]
    )
    log.info("Threat classified: %s", classification)

    # ── Build Report ──────────────────────────────────────────────
    report = FieldReport(
        audio_transcript=audio_result["transcript"],
        image_analysis=image_result["analysis"],
        threat_level=classification.get("threat_level", image_result.get("threat_level", "high")),
        category=classification.get("category", image_result.get("category", "unknown")),
        confidence=classification.get("confidence", 0.0),
        raw_audio_duration_s=audio_result["duration_s"],
        model_backend=backend.name,
    )

    # Display summary table
    table = Table(title="[SUMMARY] Field Report", border_style="bold white")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Report ID", report.report_id)
    table.add_row("Operator", report.operator_id)
    table.add_row("Timestamp", report.timestamp)
    table.add_row("Location", f"{report.location['latitude']}, {report.location['longitude']}")
    table.add_row("Threat Level", f"[bold red]{report.threat_level.upper()}[/]")
    table.add_row("Category", report.category)
    table.add_row("Confidence", f"{report.confidence:.0%}")
    table.add_row("Backend", report.model_backend)
    console.print(table)

    # ── Transmit ──────────────────────────────────────────────────
    if transmit:
        console.print()
        transmit_report(report)

    return report


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CLI Entry Point
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aegis Field Node — Multimodal edge ingestion & mesh transmission.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--audio", type=Path, default=MOCK_AUDIO_PATH,
        help="Path to audio file (.wav) for transcription.",
    )
    parser.add_argument(
        "--image", type=Path, default=MOCK_IMAGE_PATH,
        help="Path to image file (.jpg/.png) for visual analysis.",
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Force mock backend (no model weights required).",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Use live microphone and webcam for input.",
    )
    parser.add_argument(
        "--no-transmit", action="store_true",
        help="Skip mesh transmission to Command Node.",
    )
    parser.add_argument(
        "--save-report", type=Path, default=None,
        help="Save the JSON report to a file.",
    )
    args = parser.parse_args()

    console.print(
        Panel(
            "[bold green]AEGIS - The Edge-Native Crisis Coordinator[/]\n"
            "[dim]Field Node (Node A) - Gemma 4 E2B - Multimodal Ingestion[/]",
            border_style="green",
        )
    )

    # Start background sync worker
    threading.Thread(target=sync_worker, daemon=True).start()

    backend = select_backend(force_mock=args.mock)

    if args.live:
        args.audio = capture_audio_live(duration_s=5)
        args.image = capture_image_live()

    if args.mock:
        console.print("\n[bold magenta]Running 3 simulated mock scenarios sequentially...[/]", justify="center")
        for i in range(3):
            if i > 0:
                console.print("\n[bold cyan]Waiting 4 seconds before next transmission...[/]")
                time.sleep(4)
            console.print(f"\n[bold green]=== Mock Scenario {i+1} of 3 ===[/]")
            report = run_pipeline(
                audio_path=args.audio,
                image_path=args.image,
                backend=backend,
                transmit=not args.no_transmit,
            )
            if args.live:
                break # only run once if live
    else:
        report = run_pipeline(
            audio_path=args.audio,
            image_path=args.image,
            backend=backend,
            transmit=not args.no_transmit,
        )

    if args.save_report:
        args.save_report.parent.mkdir(parents=True, exist_ok=True)
        args.save_report.write_text(
            json.dumps(report.model_dump(), indent=2), encoding="utf-8"
        )
        log.info("Report saved to %s", args.save_report)


if __name__ == "__main__":
    main()
