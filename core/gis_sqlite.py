"""
Aegis — SQLite GIS Database Backend
====================================
Haversine-based spatial queries against the local SQLite database.
Used when ``USE_POSTGIS`` is False (default).
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km between two GPS points."""
    R = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class GISDatabase:
    """Thin wrapper around the local SQLite GIS database."""

    def __init__(self, db_path: Path):
        if not db_path.exists():
            raise FileNotFoundError(
                f"Database not found: {db_path}. Run setup_db.py first."
            )
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── Spatial Queries ───────────────────────────────────────────

    def query_safe_zones(
        self,
        latitude: float,
        longitude: float,
        radius_km: float = 5.0,
        zone_type: str = "any",
    ) -> list[dict]:
        conn = self._conn()
        if zone_type != "any":
            rows = conn.execute(
                "SELECT * FROM safe_zones WHERE status != 'offline' AND type = ?",
                (zone_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM safe_zones WHERE status != 'offline'"
            ).fetchall()
        conn.close()

        results = []
        for r in rows:
            d = haversine(latitude, longitude, r["latitude"], r["longitude"])
            if d <= radius_km:
                results.append({
                    **dict(r),
                    "distance_km": round(d, 2),
                    "remaining_capacity": r["capacity"] - r["current_occupancy"],
                })
        return sorted(results, key=lambda x: x["distance_km"])

    def query_hazards(
        self,
        latitude: float,
        longitude: float,
        radius_km: float = 5.0,
        min_severity: str = "low",
    ) -> list[dict]:
        sev_order = {"low": 0, "moderate": 1, "high": 2, "critical": 3}
        min_sev = sev_order.get(min_severity, 0)
        conn = self._conn()
        rows = conn.execute("SELECT * FROM hazards").fetchall()
        conn.close()

        results = []
        for r in rows:
            if sev_order.get(r["severity"], 0) < min_sev:
                continue
            d = haversine(latitude, longitude, r["latitude"], r["longitude"])
            if d <= radius_km:
                results.append({**dict(r), "distance_km": round(d, 2)})
        return sorted(results, key=lambda x: x["distance_km"])

    def query_routes(
        self,
        from_lat: float,
        from_lon: float,
        to_lat: float | None = None,
        to_lon: float | None = None,
        status_filter: str = "any",
    ) -> list[dict]:
        conn = self._conn()
        rows = conn.execute("SELECT * FROM routes").fetchall()
        conn.close()

        results = []
        for r in rows:
            if status_filter not in ("any", r["status"]):
                continue
            d_from = haversine(from_lat, from_lon, r["from_lat"], r["from_lon"])
            if d_from <= 3.0:
                entry = {**dict(r), "proximity_to_origin_km": round(d_from, 2)}
                if to_lat and to_lon:
                    entry["proximity_to_dest_km"] = round(
                        haversine(to_lat, to_lon, r["to_lat"], r["to_lon"]), 2
                    )
                results.append(entry)
        return sorted(results, key=lambda x: x["proximity_to_origin_km"])

    def query_sop(self, query: str) -> list[dict]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT title, content FROM sops WHERE sops MATCH ? ORDER BY rank LIMIT 3",
            (query,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Write Operations ──────────────────────────────────────────

    def store_field_report(self, report: dict) -> int:
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO field_reports "
            "(operator_id,timestamp,latitude,longitude,audio_transcript,"
            "image_analysis,threat_level,category,raw_payload) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                report.get("operator_id"),
                report.get("timestamp"),
                report.get("location", {}).get("latitude"),
                report.get("location", {}).get("longitude"),
                report.get("audio_transcript"),
                report.get("image_analysis"),
                report.get("threat_level"),
                report.get("category"),
                json.dumps(report),
            ),
        )
        conn.commit()
        rid = cur.lastrowid
        conn.close()
        return rid

    # ── Tool Dispatch ─────────────────────────────────────────────

    def execute_tool(self, name: str, arguments: dict) -> Any:
        dispatch = {
            "query_safe_zones": self.query_safe_zones,
            "query_hazards": self.query_hazards,
            "query_routes": self.query_routes,
            "query_sop": self.query_sop,
        }
        fn = dispatch.get(name)
        if not fn:
            return {"error": f"Unknown tool: {name}"}
        try:
            return fn(**arguments)
        except Exception as e:
            return {"error": str(e)}
