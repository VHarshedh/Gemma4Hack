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

# ── Alert Thresholds ─────────────────────────────────────────────────
# Values at or above "warning" are counted as alert readings.
ALERT_THRESHOLDS: dict[str, dict[str, float]] = {
    "air_quality": {"warning": 100.0, "critical": 200.0},
    "seismic":     {"warning": 3.5,   "critical": 5.0},
    "flood":       {"warning": 0.8,   "critical": 1.5},
    "fire":        {"warning": 250.0, "critical": 400.0},
}

# ── Sensor Definitions ───────────────────────────────────────────────

# Fixed sensor positions spread across the Cascadia Bay operational area
# (~15 km radius) so dots appear clearly separated on the map.
SENSOR_FLEET = [
    # Air Quality — north, south, east, west, centre
    {"id": "AQ-100", "lat": 46.330, "lon": -123.820, "type": "air_quality", "topic": MQTT_TOPIC_AQI,      "unit": "AQI", "base_value": 145.0, "variance": 60.0},  # North
    {"id": "AQ-101", "lat": 46.090, "lon": -123.820, "type": "air_quality", "topic": MQTT_TOPIC_AQI,      "unit": "AQI", "base_value": 175.0, "variance": 60.0},  # South
    {"id": "AQ-102", "lat": 46.210, "lon": -123.600, "type": "air_quality", "topic": MQTT_TOPIC_AQI,      "unit": "AQI", "base_value": 130.0, "variance": 60.0},  # East
    {"id": "AQ-103", "lat": 46.210, "lon": -124.040, "type": "air_quality", "topic": MQTT_TOPIC_AQI,      "unit": "AQI", "base_value": 190.0, "variance": 60.0},  # West (coast)
    {"id": "AQ-104", "lat": 46.210, "lon": -123.820, "type": "air_quality", "topic": MQTT_TOPIC_AQI,      "unit": "AQI", "base_value": 155.0, "variance": 60.0},  # Centre

    # Seismic — fault line running NW–SE
    {"id": "SZ-200", "lat": 46.320, "lon": -124.010, "type": "seismic",     "topic": MQTT_TOPIC_SEISMIC,  "unit": "Mw",  "base_value": 2.5,   "variance": 1.5},   # NW
    {"id": "SZ-201", "lat": 46.210, "lon": -123.820, "type": "seismic",     "topic": MQTT_TOPIC_SEISMIC,  "unit": "Mw",  "base_value": 3.8,   "variance": 1.5},   # Centre
    {"id": "SZ-202", "lat": 46.100, "lon": -123.630, "type": "seismic",     "topic": MQTT_TOPIC_SEISMIC,  "unit": "Mw",  "base_value": 2.1,   "variance": 1.5},   # SE

    # Flood gauges — river network (north branch, south branch, estuary)
    {"id": "FL-300", "lat": 46.290, "lon": -123.750, "type": "flood",       "topic": MQTT_TOPIC_FLOOD,    "unit": "m",   "base_value": 0.8,   "variance": 0.4},   # North river
    {"id": "FL-301", "lat": 46.150, "lon": -123.870, "type": "flood",       "topic": MQTT_TOPIC_FLOOD,    "unit": "m",   "base_value": 1.1,   "variance": 0.4},   # South river
    {"id": "FL-302", "lat": 46.180, "lon": -124.000, "type": "flood",       "topic": MQTT_TOPIC_FLOOD,    "unit": "m",   "base_value": 0.5,   "variance": 0.4},   # Coastal estuary

    # Fire/thermal — industrial zone (east) and forest edge (north-west)
    {"id": "FR-400", "lat": 46.250, "lon": -123.650, "type": "fire",        "topic": MQTT_TOPIC_FIRE,     "unit": "°C",  "base_value": 320.0, "variance": 80.0},  # Industrial east
    {"id": "FR-401", "lat": 46.280, "lon": -124.020, "type": "fire",        "topic": MQTT_TOPIC_FIRE,     "unit": "°C",  "base_value": 210.0, "variance": 80.0},  # Forest NW
]


class _Stats:
    """Accumulates per-type alert counts for the session summary."""
    def __init__(self):
        self.total: int = 0
        self.by_type: dict[str, dict[str, int]] = {
            t: {"normal": 0, "warning": 0, "critical": 0}
            for t in ALERT_THRESHOLDS
        }

    def record(self, sensor_type: str, value: float) -> str:
        self.total += 1
        thresholds = ALERT_THRESHOLDS.get(sensor_type, {})
        if thresholds and value >= thresholds["critical"]:
            level = "critical"
        elif thresholds and value >= thresholds["warning"]:
            level = "warning"
        else:
            level = "normal"
        if sensor_type in self.by_type:
            self.by_type[sensor_type][level] += 1
        return level

    def print_summary(self, duration_s: float) -> None:
        from rich.console import Console as _Console
        from rich.table import Table as _Table
        _c = _Console()
        _c.print()
        tbl = _Table(title="[bold]Sensor Network Session Summary[/]", border_style="bold white")
        tbl.add_column("Type",     style="cyan")
        tbl.add_column("Readings", justify="right")
        tbl.add_column("⚠ Warning",  justify="right", style="yellow")
        tbl.add_column("🔴 Critical", justify="right", style="red")
        tbl.add_column("✅ Normal",   justify="right", style="green")
        for stype, counts in self.by_type.items():
            total_t = counts["normal"] + counts["warning"] + counts["critical"]
            tbl.add_row(
                stype.replace("_", " ").title(),
                str(total_t),
                str(counts["warning"]),
                str(counts["critical"]),
                str(counts["normal"]),
            )
        _c.print(tbl)
        alerts = sum(c["warning"] + c["critical"] for c in self.by_type.values())
        _c.print(
            f"\n[bold]Total readings:[/] {self.total}  |  "
            f"[yellow]Alerts fired:[/] [bold yellow]{alerts}[/]  |  "
            f"[dim]Duration: {duration_s:.0f}s[/]\n"
        )


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

async def stream_mqtt(stats: _Stats, deadline: float | None):
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
        if deadline and time.monotonic() >= deadline:
            break
        try:
            async with aiomqtt.Client(MQTT_HOST, MQTT_PORT) as client:
                log.info("Connected to MQTT broker.")
                while True:
                    if deadline and time.monotonic() >= deadline:
                        return
                    for s in SENSOR_FLEET:
                        payload = _reading(s)
                        topic = s["topic"]
                        await client.publish(topic, json.dumps(payload))
                        level = stats.record(s["type"], payload["value"])
                        _log_reading(payload, level)
                    await asyncio.sleep(2.0)
        except Exception as e:
            if deadline and time.monotonic() >= deadline:
                break
            log.warning("MQTT connection lost (%s). Reconnecting in 5s …", e)
            await asyncio.sleep(5.0)


# ── HTTP Fallback Mode ───────────────────────────────────────────────

async def stream_http(stats: _Stats, deadline: float | None):
    """Legacy HTTP POST mode (original sensor_network.py behaviour)."""
    url = f"{COMMAND_NODE_URL}/api/v1/sensor-data"
    log.info(
        "📡 Starting HTTP Smart City Sensor Network → %s (%d sensors)",
        url, len(SENSOR_FLEET),
    )

    async with httpx.AsyncClient() as client:
        while True:
            if deadline and time.monotonic() >= deadline:
                break
            for s in SENSOR_FLEET:
                payload = _reading(s)
                try:
                    await client.post(url, json=payload, timeout=5.0)
                    level = stats.record(s["type"], payload["value"])
                    _log_reading(payload, level)
                except Exception as e:
                    log.error("HTTP send error: %s", e)
            await asyncio.sleep(2.0)


def _log_reading(p: dict, level: str = "normal") -> None:
    icons = {"air_quality": "💨", "seismic": "🌍", "flood": "🌊", "fire": "🔥"}
    alert_tag = {"warning": " ⚠️", "critical": " 🔴"}.get(level, "")
    icon = icons.get(p["type"], "📡")
    log.info(
        "%s [%s] %s: %.2f %s%s",
        icon, p["sensor_id"], p["type"], p["value"], p["unit"], alert_tag,
    )


# ── Entry Point ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Aegis Smart City IoT Sensor Network")
    parser.add_argument("--http", action="store_true", help="Use legacy HTTP mode instead of MQTT")
    parser.add_argument(
        "--duration", type=int, default=None, metavar="SECONDS",
        help="Run for N seconds then print an alert summary and exit (default: run forever)",
    )
    args = parser.parse_args()

    stats = _Stats()
    deadline = (time.monotonic() + args.duration) if args.duration else None
    start = time.monotonic()

    try:
        if args.http:
            asyncio.run(stream_http(stats, deadline))
        else:
            loop = asyncio.SelectorEventLoop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(stream_mqtt(stats, deadline))
            finally:
                loop.close()
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
    finally:
        if args.duration or stats.total > 0:
            stats.print_summary(time.monotonic() - start)


if __name__ == "__main__":
    main()
