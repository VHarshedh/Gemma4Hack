"""
Aegis — Edge Inference Backends
===============================
LiteRT, Cactus, and Mock backends for Gemma 4 E2B.
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from config import (
    FIELD_MODEL_PATH, MAX_TOKENS, TEMPERATURE, TOP_K, TOP_P,
)

log = logging.getLogger("aegis.edge.backends")

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
        """Combined classification."""


class LiteRTBackend(InferenceBackend):
    """Google LiteRT LM backend for Gemma 4 E2B."""
    name = "litert_lm"

    def __init__(self, model_path: Path) -> None:
        try:
            import litert_lm
        except ImportError:
            raise RuntimeError("litert_lm not installed.")
        log.info("Loading LiteRT LM model from %s …", model_path)
        self._model = litert_lm.load(str(model_path))

    def transcribe_audio(self, audio_path: Path) -> dict[str, Any]:
        import soundfile as sf
        audio_data, sample_rate = sf.read(str(audio_path))
        duration = len(audio_data) / sample_rate
        prompt = "Transcribe the following audio exactly. Output ONLY the transcription."
        result = self._model.generate(
            prompt=prompt, audio=audio_data, temperature=TEMPERATURE, 
            top_p=TOP_P, top_k=TOP_K, max_tokens=MAX_TOKENS
        )
        return {"transcript": result.strip(), "duration_s": round(duration, 2)}

    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        from PIL import Image
        image = Image.open(image_path)
        prompt = "Analyze this image for structural damage, hazards, severity, and survivors. 2-3 sentences."
        result = self._model.generate(
            prompt=prompt, image=image, temperature=TEMPERATURE,
            top_p=TOP_P, top_k=TOP_K, max_tokens=MAX_TOKENS
        )
        return self._parse_analysis(result.strip())

    def classify_threat(self, transcript: str, image_analysis: str) -> dict[str, Any]:
        prompt = (
            f"Classify threat. Audio: {transcript}\nVisual: {image_analysis}\n"
            "JSON: {\"threat_level\": \"...\", \"category\": \"...\", \"confidence\": 0.0}"
        )
        result = self._model.generate(prompt=prompt, max_tokens=256)
        try:
            return json.loads(result.strip())
        except Exception:
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


class CactusBackend(InferenceBackend):
    """Cactus inference engine backend."""
    name = "cactus"

    def __init__(self, model_path: Path) -> None:
        try:
            from cactus import cactus_init
        except ImportError:
            raise RuntimeError("cactus not installed.")
        self._model = cactus_init(str(model_path), None, False)

    def _complete(self, messages: list[dict]) -> str:
        from cactus import cactus_complete
        payload = json.dumps(messages)
        result = json.loads(cactus_complete(self._model, payload, None, None, None))
        return result.get("response", "")

    def transcribe_audio(self, audio_path: Path) -> dict[str, Any]:
        import soundfile as sf
        audio_data, sample_rate = sf.read(str(audio_path))
        duration = len(audio_data) / sample_rate
        audio_b64 = base64.b64encode(audio_data.astype("float32").tobytes()).decode()
        messages = [
            {"role": "system", "content": "Transcribe audio."},
            {"role": "user", "content": "Transcribe exactly.", "audio": audio_b64},
        ]
        return {"transcript": self._complete(messages).strip(), "duration_s": round(duration, 2)}

    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        image_b64 = base64.b64encode(image_path.read_bytes()).decode()
        messages = [
            {"role": "system", "content": "Analyze image."},
            {"role": "user", "content": "Analyze hazards.", "image": image_b64},
        ]
        analysis = self._complete(messages)
        return LiteRTBackend._parse_analysis(analysis)

    def classify_threat(self, transcript: str, image_analysis: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": "Classify threat JSON."},
            {"role": "user", "content": f"Audio: {transcript}\nVisual: {image_analysis}"},
        ]
        try:
            return json.loads(self._complete(messages))
        except Exception:
            return {"threat_level": "high", "category": "unknown", "confidence": 0.5}


class MockBackend(InferenceBackend):
    """Deterministic mock backend."""
    name = "mock"
    
    def __init__(self):
        try:
            from faker import Faker
            self.fake = Faker()
        except ImportError:
            raise RuntimeError("faker library not installed.")
        self.current_scenario = self._generate_scenario()

    def _generate_scenario(self) -> dict[str, Any]:
        disasters = [
            ("structural collapse", "critical", "collapse"),
            ("flash flood", "high", "flood"),
            ("chemical spill", "critical", "hazmat"),
            ("wildfire", "high", "fire"),
        ]
        dtype, tlevel, cat = secrets.choice(disasters)
        audio = f"Command, confirmed {dtype}. Multiple casualties. Requesting backup."
        image = f"Visual of severe {dtype}. Scene is chaotic."
        return {
            "audio": audio, "duration": 25.0, "image": image,
            "threat_level": tlevel, "category": cat,
            "full_category": dtype.replace(" ", "_"),
            "confidence": 0.95
        }

    def transcribe_audio(self, audio_path: Path) -> dict[str, Any]:
        self.current_scenario = self._generate_scenario()
        return {"transcript": self.current_scenario["audio"], "duration_s": 25.0}

    def analyze_image(self, image_path: Path) -> dict[str, Any]:
        return {
            "analysis": self.current_scenario["image"],
            "threat_level": self.current_scenario["threat_level"],
            "category": self.current_scenario["category"],
        }

    def classify_threat(self, transcript: str, image_analysis: str) -> dict[str, Any]:
        return {
            "threat_level": self.current_scenario["threat_level"],
            "category": self.current_scenario["full_category"],
            "confidence": self.current_scenario["confidence"],
        }


def select_backend(force_mock: bool = False) -> InferenceBackend:
    if force_mock:
        return MockBackend()
    if FIELD_MODEL_PATH.exists() and FIELD_MODEL_PATH.suffix == ".litertlm":
        try: return LiteRTBackend(FIELD_MODEL_PATH)
        except Exception: pass
    if FIELD_MODEL_PATH.exists():
        try: return CactusBackend(FIELD_MODEL_PATH)
        except Exception: pass
    return MockBackend()
