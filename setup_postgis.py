#!/usr/bin/env python3
"""
Aegis — PostGIS Database Bootstrap (Phase 1 Upgrade)
=====================================================
Mirrors setup_db.py but uses PostgreSQL + PostGIS for true geospatial
queries (ST_DWithin, ST_Distance, geometry columns) instead of Python-
side Haversine calculations.

Usage
-----
    python setup_postgis.py          # create tables & seed data
    python setup_postgis.py --reset  # drop & recreate everything
"""
from __future__ import annotations

import argparse
import sys

import psycopg2
from psycopg2.extras import execute_values

from config import PG_DSN


# ─── Schema ───────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS safe_zones (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL CHECK (type IN ('shelter','hospital','staging_area','fire_station')),
    geom            GEOMETRY(Point, 4326) NOT NULL,
    capacity        INTEGER NOT NULL DEFAULT 0,
    current_occupancy INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL CHECK (status IN ('operational','limited','offline')) DEFAULT 'operational',
    has_medical     BOOLEAN NOT NULL DEFAULT FALSE,
    has_power       BOOLEAN NOT NULL DEFAULT TRUE,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_safe_zones_geom ON safe_zones USING GIST (geom);

CREATE TABLE IF NOT EXISTS hazards (
    id              SERIAL PRIMARY KEY,
    type            TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('low','moderate','high','critical')),
    geom            GEOMETRY(Point, 4326) NOT NULL,
    radius_m        REAL NOT NULL DEFAULT 100.0,
    description     TEXT,
    reported_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    verified        BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_hazards_geom ON hazards USING GIST (geom);

CREATE TABLE IF NOT EXISTS routes (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    from_location   TEXT NOT NULL,
    to_location     TEXT NOT NULL,
    from_geom       GEOMETRY(Point, 4326) NOT NULL,
    to_geom         GEOMETRY(Point, 4326) NOT NULL,
    distance_km     REAL NOT NULL,
    estimated_time_min INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('clear','blocked','partial')) DEFAULT 'clear',
    road_type       TEXT NOT NULL DEFAULT 'primary',
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_routes_from ON routes USING GIST (from_geom);

CREATE TABLE IF NOT EXISTS field_reports (
    id              SERIAL PRIMARY KEY,
    operator_id     TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    geom            GEOMETRY(Point, 4326),
    audio_transcript TEXT,
    image_analysis  TEXT,
    threat_level    TEXT CHECK (threat_level IN ('low','moderate','high','critical')),
    category        TEXT,
    raw_payload     JSONB,
    processed       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS sensor_readings (
    id              SERIAL PRIMARY KEY,
    sensor_id       TEXT NOT NULL,
    geom            GEOMETRY(Point, 4326) NOT NULL,
    sensor_type     TEXT NOT NULL,
    value           REAL NOT NULL,
    unit            TEXT NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sensor_geom ON sensor_readings USING GIST (geom);

-- FTS for SOPs (using tsvector instead of FTS5)
CREATE TABLE IF NOT EXISTS sops (
    id      SERIAL PRIMARY KEY,
    title   TEXT NOT NULL,
    content TEXT NOT NULL,
    tsv     TSVECTOR GENERATED ALWAYS AS (
                to_tsvector('english', title || ' ' || content)
            ) STORED
);
CREATE INDEX IF NOT EXISTS idx_sops_tsv ON sops USING GIN (tsv);
"""

# ─── Seed Data (same as setup_db.py) ─────────────────────────────────

SAFE_ZONES = [
    ("Cascadia Bay High School", "shelter", 46.2104, -123.8165, 800, 312, "operational", True, True, "Main civic shelter. Generator-powered. Red Cross on site."),
    ("St. Mary's Regional Hospital", "hospital", 46.2051, -123.8098, 450, 389, "limited", True, True, "ER at capacity. Accepting critical patients only."),
    ("Harbor Park Staging Area", "staging_area", 46.2189, -123.8234, 1200, 145, "operational", False, False, "Open-air staging. No power. Supplies arriving via helicopter."),
    ("Firehouse #7 — Downtown", "fire_station", 46.2073, -123.8112, 60, 42, "operational", True, True, "SAR command post. Limited civilian intake."),
    ("Oceanview Community Center", "shelter", 46.2220, -123.8300, 500, 67, "operational", False, True, "Secondary shelter. Cots and blankets available."),
    ("Cascadia Bay Veterans Hall", "shelter", 46.2005, -123.8050, 350, 280, "limited", True, False, "Generator failed at 14:00. Medical supplies low."),
    ("Pacific Ridge Elementary", "shelter", 46.2150, -123.8400, 400, 55, "operational", False, True, "Recently opened. Accessible via Route 7 only."),
    ("Bayfront Medical Clinic", "hospital", 46.2130, -123.8180, 120, 118, "limited", True, True, "Walk-in clinic. Two physicians on duty."),
]

HAZARDS = [
    ("collapsed_structure", "critical", 46.2060, -123.8130, 200, "6-storey parking garage fully collapsed. Possible survivors trapped.", True),
    ("gas_leak", "high", 46.2085, -123.8145, 150, "Ruptured 6-inch gas main on 4th & Harbor. Active leak, ignition risk.", True),
    ("flood_zone", "moderate", 46.2200, -123.8250, 500, "Columbia Creek overflow. 0.5 m standing water on River Road.", True),
    ("chemical_spill", "critical", 46.2120, -123.8090, 300, "Industrial solvent tank breach at Cascadia Chemical. HazMat deployed.", True),
    ("downed_power_line", "high", 46.2095, -123.8200, 100, "Live 12 kV line across Oak Street. Energised — keep 30 m clearance.", True),
    ("fire", "high", 46.2040, -123.8070, 250, "Structure fire in warehouse district. 3 buildings involved. FD on scene.", False),
    ("landslide", "moderate", 46.2170, -123.8350, 180, "Hillside slump blocking Ridge Road. Single lane passable.", True),
    ("tsunami_risk", "low", 46.2250, -123.8400, 1000, "Advisory: Tsunami watch for coastline. No wave detected yet.", False),
    ("collapsed_structure", "high", 46.2030, -123.8110, 120, "Partial collapse of residential block. 3 units affected.", True),
    ("gas_leak", "moderate", 46.2160, -123.8280, 80, "Minor residential gas leak. Utility crew en route.", False),
]

ROUTES = [
    ("Route Alpha — Harbor to School Shelter", "Harbor Park Staging", "Cascadia Bay High School", 46.2189, -123.8234, 46.2104, -123.8165, 2.1, 8, "clear", "primary", "Main evacuation corridor. National Guard escorted."),
    ("Route Bravo — Downtown to Hospital", "Firehouse #7", "St. Mary's Hospital", 46.2073, -123.8112, 46.2051, -123.8098, 0.8, 4, "partial", "secondary", "Debris on 3rd St. One lane open."),
    ("Route Charlie — Coast Road North", "Oceanview Center", "Pacific Ridge Elementary", 46.2220, -123.8300, 46.2150, -123.8400, 3.5, 15, "clear", "primary", "Coastal highway. Clear but monitor tsunami advisory."),
    ("Route Delta — Veterans Hall Evacuation", "Veterans Hall", "Harbor Park Staging", 46.2005, -123.8050, 46.2189, -123.8234, 4.2, 20, "blocked", "primary", "BLOCKED — Parking garage collapse debris across intersection."),
    ("Route Echo — Ridge Road Bypass", "Pacific Ridge Elementary", "Cascadia Bay High School", 46.2150, -123.8400, 46.2104, -123.8165, 5.8, 25, "partial", "tertiary", "Landslide narrows road. Single lane. 4x4 recommended."),
    ("Route Foxtrot — Medical Corridor", "Bayfront Clinic", "St. Mary's Hospital", 46.2130, -123.8180, 46.2051, -123.8098, 1.5, 6, "clear", "secondary", "Ambulance-priority route. Civilian traffic restricted."),
    ("Route Golf — Industrial Bypass", "Harbor Park Staging", "Veterans Hall", 46.2189, -123.8234, 46.2005, -123.8050, 6.1, 30, "partial", "tertiary", "Bypasses collapsed garage via industrial park. Chemical spill nearby — masks required."),
]

SOPS = [
    ("HazMat Response Protocol", "If a chemical spill or hazardous material is detected, all unequipped personnel must maintain a 300m clearance. Use Route Golf for industrial bypass but only with respirator masks. Primary hazmat staging is Firehouse #7."),
    ("Tsunami Evacuation Guidelines", "In the event of a tsunami risk, immediately evacuate all personnel and civilians to ground at least 30m above sea level. Coastal routes like Route Charlie must be cleared and monitored. Direct evacuees to inland shelters such as Pacific Ridge Elementary."),
    ("Structural Collapse Triage", "For collapsed structures, establish a 150m perimeter to prevent secondary collapse casualties. If gas leaks are detected nearby (e.g. within 200m), prohibit all spark-producing equipment. Dispatch Urban SAR teams immediately."),
]


def create_database(*, reset: bool = False) -> None:
    """Create PostGIS schema and seed data."""
    try:
        conn = psycopg2.connect(PG_DSN)
    except psycopg2.OperationalError:
        print(
            "\n[postgis] ERROR: Cannot connect to PostgreSQL at "
            f"{PG_DSN.split('password=')[0]}***\n"
            "  Make sure the PostGIS container is running:\n"
            "    docker compose up -d gis-db\n"
        )
        raise SystemExit(1)
    conn.autocommit = True
    cur = conn.cursor()

    if reset:
        print("[postgis] Dropping existing tables …")
        for t in ("sensor_readings", "field_reports", "routes", "hazards", "safe_zones", "sops"):
            cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")

    print("[postgis] Creating schema with PostGIS extensions …")
    cur.execute(SCHEMA_SQL)

    # ── safe_zones ────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM safe_zones")
    if cur.fetchone()[0] == 0:
        print(f"[postgis] Inserting {len(SAFE_ZONES)} safe zones …")
        for z in SAFE_ZONES:
            name, ztype, lat, lon, cap, occ, status, med, pwr, notes = z
            cur.execute(
                """INSERT INTO safe_zones
                   (name,type,geom,capacity,current_occupancy,status,has_medical,has_power,notes)
                   VALUES (%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326),%s,%s,%s,%s,%s,%s)""",
                (name, ztype, lon, lat, cap, occ, status, med, pwr, notes),
            )

    # ── hazards ───────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM hazards")
    if cur.fetchone()[0] == 0:
        print(f"[postgis] Inserting {len(HAZARDS)} hazards …")
        for h in HAZARDS:
            htype, sev, lat, lon, radius, desc, verified = h
            cur.execute(
                """INSERT INTO hazards
                   (type,severity,geom,radius_m,description,verified)
                   VALUES (%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326),%s,%s,%s)""",
                (htype, sev, lon, lat, radius, desc, verified),
            )

    # ── routes ────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM routes")
    if cur.fetchone()[0] == 0:
        print(f"[postgis] Inserting {len(ROUTES)} routes …")
        for r in ROUTES:
            name, fl, tl, flat, flon, tlat, tlon, dist, t, st, rt, notes = r
            cur.execute(
                """INSERT INTO routes
                   (name,from_location,to_location,from_geom,to_geom,
                    distance_km,estimated_time_min,status,road_type,notes)
                   VALUES (%s,%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326),
                           ST_SetSRID(ST_MakePoint(%s,%s),4326),%s,%s,%s,%s,%s)""",
                (name, fl, tl, flon, flat, tlon, tlat, dist, t, st, rt, notes),
            )

    # ── sops ──────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM sops")
    if cur.fetchone()[0] == 0:
        print(f"[postgis] Inserting {len(SOPS)} SOPs …")
        for title, content in SOPS:
            cur.execute("INSERT INTO sops (title,content) VALUES (%s,%s)", (title, content))

    conn.close()
    print("[postgis] ✅ Database ready.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aegis — PostGIS database bootstrap")
    parser.add_argument("--reset", action="store_true", help="Drop & recreate all tables")
    args = parser.parse_args()
    create_database(reset=args.reset)
