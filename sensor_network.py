#!/usr/bin/env python3
"""
Aegis — Smart City IoT Sensor Network (Phase 1 Upgrade)
========================================================
Publishes simulated sensor telemetry over MQTT to the Mosquitto broker.
Supports four sensor types: air quality, seismic, flood, and fire.

Falls back to the original HTTP POST mode when MQTT is unavailable.

Usage
-----
    python sensor_network.py              # MQTT mode (default)
    python sensor_network.py --http       # legacy HTTP mode
"""
from __future__ import annotations

import asyncio
import argparse
import json
import logging
import random
import time
from datetime import datetime, timezone

import httpx

from config import (
    COMMAND_NODE_URL,
    MQTT_HOST,
    MQTT_PORT,
    MQTT_TOPIC_AQI,
    MQTT_TOPIC_SEISMIC,
    MQTT_TOPIC_FLOOD,
    MQTT_TOPIC_FIRE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s|%(name)-16s|%(levelname)-7s|%(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aegis.sensors")

# ── Sensor Definitions ───────────────────────────────────────────────

SENSOR_FLEET = [
    # Air Quality sensors
    *[
        {
            "id": f"AQ-{100+i}",
            "lat": 46.21 + random.uniform(-0.015, 0.015),
            "lon": -123.82 + random.uniform(-0.015, 0.015),
            "type": "air_quality",
            "topic": MQTT_TOPIC_AQI,
            "unit": "AQI",
            "base_value": random.uniform(120.0, 200.0),
            "variance": 60.0,
        }
        for i in range(5)
    ],
    # Seismic sensors
    *[
        {
            "id": f"SZ-{200+i}",
            "lat": 46.21 + random.uniform(-0.02, 0.02),
            "lon": -123.82 + random.uniform(-0.02, 0.02),
            "type": "seismic",
            "topic": MQTT_TOPIC_SEISMIC,
            "unit": "Mw",
            "base_value": random.uniform(2.0, 5.0),
            "variance": 1.5,
        }
        for i in range(3)
    ],
    # Flood / water-level gauges
    *[
        {
            "id": f"FL-{300+i}",
            "lat": 46.22 + random.uniform(-0.005, 0.005),
            "lon": -123.825 + random.uniform(-0.005, 0.005),
            "type": "flood",
            "topic": MQTT_TOPIC_FLOOD,
            "unit": "m",
            "base_value": random.uniform(0.3, 1.2),
            "variance": 0.4,
        }
        for i in range(3)
    ],
    # Fire / thermal sensors
    *[
        {
            "id": f"FR-{400+i}",
            "lat": 46.204 + random.uniform(-0.005, 0.005),
            "lon": -123.807 + random.uniform(-0.005, 0.005),
            "type": "fire",
            "topic": MQTT_TOPIC_FIRE,
            "unit": "°C",
            "base_value": random.uniform(180.0, 400.0),
            "variance": 80.0,
        }
        for i in range(2)
    ],
]


def _reading(sensor: dict) -> dict:
    """Generate a single sensor reading payload."""
    value = sensor["base_value"] + random.uniform(
        -sensor["variance"], sensor["variance"]
    )
    return {
        "sensor_id": sensor["id"],
        "latitude": round(sensor["lat"], 6),
        "longitude": round(sensor["lon"], 6),
        "type": sensor["type"],
        "value": round(max(0, value), 2),
        "unit": sensor["unit"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── MQTT Mode ─────────────────────────────────────────────────────────

async def stream_mqtt():
    """Publish sensor data to Mosquitto via MQTT."""
    try:
        import aiomqtt
    except ImportError:
        log.error("aiomqtt not installed. Run: pip install aiomqtt")
        return

    log.info(
        "📡 Starting MQTT Smart City Sensor Network → %s:%d (%d sensors)",
        MQTT_HOST, MQTT_PORT, len(SENSOR_FLEET),
    )

    while True:
        try:
            async with aiomqtt.Client(MQTT_HOST, MQTT_PORT) as client:
                log.info("Connected to MQTT broker.")
                while True:
                    for s in SENSOR_FLEET:
                        payload = _reading(s)
                        topic = s["topic"]
                        await client.publish(topic, json.dumps(payload))
                        _log_reading(payload)
                    await asyncio.sleep(2.0)
        except Exception as e:
            log.warning("MQTT connection lost (%s). Reconnecting in 5s …", e)
            await asyncio.sleep(5.0)


# ── HTTP Fallback Mode ───────────────────────────────────────────────

async def stream_http():
    """Legacy HTTP POST mode (original sensor_network.py behaviour)."""
    url = f"{COMMAND_NODE_URL}/api/v1/sensor-data"
    log.info(
        "📡 Starting HTTP Smart City Sensor Network → %s (%d sensors)",
        url, len(SENSOR_FLEET),
    )

    async with httpx.AsyncClient() as client:
        while True:
            for s in SENSOR_FLEET:
                payload = _reading(s)
                try:
                    await client.post(url, json=payload, timeout=5.0)
                    _log_reading(payload)
                except Exception as e:
                    log.error("HTTP send error: %s", e)
            await asyncio.sleep(2.0)


def _log_reading(p: dict) -> None:
    icons = {"air_quality": "💨", "seismic": "🌍", "flood": "🌊", "fire": "🔥"}
    icon = icons.get(p["type"], "📡")
    log.info(
        "%s [%s] %s: %.2f %s",
        icon, p["sensor_id"], p["type"], p["value"], p["unit"],
    )


# ── Entry Point ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Aegis Smart City IoT Sensor Network")
    parser.add_argument("--http", action="store_true", help="Use legacy HTTP mode instead of MQTT")
    args = parser.parse_args()

    if args.http:
        asyncio.run(stream_http())
    else:
        asyncio.run(stream_mqtt())


if __name__ == "__main__":
    main()
