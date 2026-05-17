"""
Aegis — Shared Pydantic Data Models
====================================
All data models used across both the command node and field node.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


# ── Field Report (used by both nodes) ─────────────────────────────

class FieldReport(BaseModel):
    """Structured field report transmitted from Node A to Node B."""

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


# ── API Request Payloads (used by command node) ───────────────────

class FieldReportPayload(BaseModel):
    """Incoming field report payload for the REST API."""
    report_id: str = ""
    operator_id: str = ""
    timestamp: str = ""
    location: dict = Field(default_factory=dict)
    audio_transcript: str = ""
    image_analysis: str = ""
    threat_level: str = "unknown"
    category: str = "unclassified"
    confidence: float = 0.0
    raw_audio_duration_s: float = 0.0
    model_backend: str = "unknown"


class SensorDataPayload(BaseModel):
    """Incoming IoT sensor reading payload."""
    sensor_id: str
    latitude: float
    longitude: float
    type: str
    value: float
    unit: str


class VoiceCommandPayload(BaseModel):
    """Commander voice command payload."""
    text: str
