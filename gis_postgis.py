"""
Aegis — PostGIS GIS Database Backend (Phase 1)
================================================
Drop-in replacement for the SQLite GISDatabase class in command_node.py.
Uses PostgreSQL + PostGIS for true geospatial queries (ST_DWithin,
ST_Distance_Sphere) instead of Python-side Haversine.

This module is imported by command_node.py when USE_POSTGIS is True.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import psycopg2
import psycopg2.extras

from config import PG_DSN

log = logging.getLogger("aegis.postgis")


class PostGISDatabase:
    """PostGIS-backed GIS database with true spatial queries."""

    def __init__(self, dsn: str = PG_DSN):
        self.dsn = dsn
        # Verify connectivity on init
        conn = self._conn()
        conn.close()
        log.info("PostGIS backend connected: %s", dsn.split("password=")[0] + "***")

    def _conn(self) -> psycopg2.extensions.connection:
        conn = psycopg2.connect(self.dsn)
        conn.autocommit = True
        return conn

    # ── Public query methods (same interface as SQLite GISDatabase) ──

    def query_safe_zones(
        self,
        latitude: float,
        longitude: float,
        radius_km: float = 5.0,
        zone_type: str = "any",
    ) -> list[dict]:
        """Find safe zones within *radius_km* using ST_DWithin."""
        conn = self._conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        radius_m = radius_km * 1000

        sql = """
            SELECT id, name, type,
                   ST_Y(geom) AS latitude, ST_X(geom) AS longitude,
                   capacity, current_occupancy, status,
                   has_medical, has_power, notes,
                   ROUND(ST_Distance_Sphere(
                       geom,
                       ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                   )::numeric / 1000, 2) AS distance_km,
                   capacity - current_occupancy AS remaining_capacity
            FROM safe_zones
            WHERE status != 'offline'
              AND ST_DWithin(
                  geom::geography,
                  ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                  %s
              )
        """
        params = [longitude, latitude, longitude, latitude, radius_m]

        if zone_type != "any":
            sql += " AND type = %s"
            params.append(zone_type)

        sql += " ORDER BY distance_km"

        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def query_hazards(
        self,
        latitude: float,
        longitude: float,
        radius_km: float = 5.0,
        min_severity: str = "low",
    ) -> list[dict]:
        """Find hazards within radius, filtered by minimum severity."""
        sev_order = {"low": 0, "moderate": 1, "high": 2, "critical": 3}
        min_sev = sev_order.get(min_severity, 0)

        conn = self._conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        radius_m = radius_km * 1000

        cur.execute(
            """
            SELECT id, type, severity,
                   ST_Y(geom) AS latitude, ST_X(geom) AS longitude,
                   radius_m, description, reported_at, verified,
                   ROUND(ST_Distance_Sphere(
                       geom,
                       ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                   )::numeric / 1000, 2) AS distance_km
            FROM hazards
            WHERE ST_DWithin(
                geom::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                %s
            )
            ORDER BY distance_km
            """,
            (longitude, latitude, longitude, latitude, radius_m),
        )

        rows = []
        for r in cur.fetchall():
            d = dict(r)
            if sev_order.get(d["severity"], 0) >= min_sev:
                # Convert reported_at to string for JSON serialisation
                if d.get("reported_at"):
                    d["reported_at"] = str(d["reported_at"])
                rows.append(d)
        conn.close()
        return rows

    def query_routes(
        self,
        from_lat: float,
        from_lon: float,
        to_lat: float | None = None,
        to_lon: float | None = None,
        status_filter: str = "any",
    ) -> list[dict]:
        """Find evacuation routes whose origin is near the given point."""
        conn = self._conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        sql = """
            SELECT id, name, from_location, to_location,
                   ST_Y(from_geom) AS from_lat, ST_X(from_geom) AS from_lon,
                   ST_Y(to_geom) AS to_lat, ST_X(to_geom) AS to_lon,
                   distance_km, estimated_time_min, status, road_type, notes,
                   ROUND(ST_Distance_Sphere(
                       from_geom,
                       ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                   )::numeric / 1000, 2) AS proximity_to_origin_km
            FROM routes
            WHERE ST_DWithin(
                from_geom::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                3000
            )
        """
        params: list[Any] = [from_lon, from_lat, from_lon, from_lat]

        if status_filter != "any":
            sql += " AND status = %s"
            params.append(status_filter)

        sql += " ORDER BY proximity_to_origin_km"

        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]

        # Add destination proximity if requested
        if to_lat is not None and to_lon is not None:
            for r in rows:
                conn2 = self._conn()
                cur2 = conn2.cursor()
                cur2.execute(
                    """SELECT ROUND(ST_Distance_Sphere(
                           ST_SetSRID(ST_MakePoint(%s,%s),4326),
                           ST_SetSRID(ST_MakePoint(%s,%s),4326)
                       )::numeric / 1000, 2)""",
                    (r["to_lon"], r["to_lat"], to_lon, to_lat),
                )
                r["proximity_to_dest_km"] = float(cur2.fetchone()[0])
                conn2.close()

        conn.close()
        return rows

    def query_sop(self, query: str) -> list[dict]:
        """Full-text search on SOPs using PostgreSQL tsvector."""
        conn = self._conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Convert the user query into a tsquery (OR between words)
        ts_query = " | ".join(query.split())
        cur.execute(
            """SELECT title, content,
                      ts_rank(tsv, to_tsquery('english', %s)) AS rank
               FROM sops
               WHERE tsv @@ to_tsquery('english', %s)
               ORDER BY rank DESC LIMIT 3""",
            (ts_query, ts_query),
        )
        rows = [{"title": r["title"], "content": r["content"]} for r in cur.fetchall()]
        conn.close()
        return rows

    def store_field_report(self, report: dict) -> int:
        """Insert a field report and return its row ID."""
        conn = self._conn()
        cur = conn.cursor()
        lat = report.get("location", {}).get("latitude")
        lon = report.get("location", {}).get("longitude")
        geom_expr = "ST_SetSRID(ST_MakePoint(%s,%s),4326)" if lat and lon else "NULL"

        sql = f"""
            INSERT INTO field_reports
                (operator_id, timestamp, geom, audio_transcript, image_analysis,
                 threat_level, category, raw_payload)
            VALUES (%s, %s, {geom_expr}, %s, %s, %s, %s, %s)
            RETURNING id
        """
        params = [
            report.get("operator_id"),
            report.get("timestamp"),
        ]
        if lat and lon:
            params += [lon, lat]
        params += [
            report.get("audio_transcript"),
            report.get("image_analysis"),
            report.get("threat_level"),
            report.get("category"),
            json.dumps(report),
        ]

        cur.execute(sql, params)
        rid = cur.fetchone()[0]
        conn.close()
        return rid

    def store_sensor_reading(
        self, sensor_id: str, lat: float, lon: float,
        sensor_type: str, value: float, unit: str,
    ) -> int:
        """Store an IoT sensor reading with geospatial point."""
        conn = self._conn()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO sensor_readings
                   (sensor_id, geom, sensor_type, value, unit)
               VALUES (%s, ST_SetSRID(ST_MakePoint(%s,%s),4326), %s, %s, %s)
               RETURNING id""",
            (sensor_id, lon, lat, sensor_type, value, unit),
        )
        rid = cur.fetchone()[0]
        conn.close()
        return rid

    def execute_tool(self, name: str, arguments: dict) -> Any:
        """Dispatch a tool call to the correct query method."""
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
