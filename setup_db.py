#!/usr/bin/env python3
"""
Aegis: Edge-Native Crisis Coordinator
======================================
Database Bootstrap — ``setup_db.py``

Generates the mock SQLite GIS database (``local_gis.db``) that the
Command Center (Node B) queries via Gemma 4's native function-calling
capability.

Scenario
--------
A 7.2-magnitude earthquake has struck the fictional city of **Cascadia
Bay** (loosely modelled on a Pacific Northwest coastal city).  The mock
data represents:

* **safe_zones** — Verified shelters, hospitals, and staging areas with
  current capacity and operational status.
* **hazards** — Known dangers: collapsed structures, gas leaks, flood
  zones, chemical spills, and downed power lines.
* **routes** — Pre-planned evacuation corridors with real-time status
  (clear / blocked / partially passable) and estimated travel times.
* **field_reports** — Placeholder table for incoming reports ingested
  from Field Nodes.

Usage
-----
    python setup_db.py          # creates ./data/local_gis.db
    python setup_db.py --reset  # drops & re-creates all tables
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from config import DATABASE_PATH


# ─── Schema Definitions ──────────────────────────────────────────────

SCHEMA_SQL = """
-- Safe zones: shelters, hospitals, staging areas
CREATE TABLE IF NOT EXISTS safe_zones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    type        TEXT    NOT NULL CHECK (type IN ('shelter', 'hospital', 'staging_area', 'fire_station')),
    latitude    REAL    NOT NULL,
    longitude   REAL    NOT NULL,
    capacity    INTEGER NOT NULL DEFAULT 0,
    current_occupancy INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL CHECK (status IN ('operational', 'limited', 'offline')) DEFAULT 'operational',
    has_medical INTEGER NOT NULL DEFAULT 0,   -- boolean 0/1
    has_power   INTEGER NOT NULL DEFAULT 1,
    notes       TEXT
);

-- Known hazards in the affected area
CREATE TABLE IF NOT EXISTS hazards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT    NOT NULL CHECK (type IN (
                    'collapsed_structure', 'gas_leak', 'flood_zone',
                    'chemical_spill', 'downed_power_line', 'fire',
                    'landslide', 'tsunami_risk'
                )),
    severity    TEXT    NOT NULL CHECK (severity IN ('low', 'moderate', 'high', 'critical')),
    latitude    REAL    NOT NULL,
    longitude   REAL    NOT NULL,
    radius_m    REAL    NOT NULL DEFAULT 100.0,   -- danger radius in metres
    description TEXT,
    reported_at TEXT    NOT NULL DEFAULT (datetime('now')),
    verified    INTEGER NOT NULL DEFAULT 0
);

-- Evacuation routes between key points
CREATE TABLE IF NOT EXISTS routes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    from_location   TEXT    NOT NULL,
    to_location     TEXT    NOT NULL,
    from_lat        REAL    NOT NULL,
    from_lon        REAL    NOT NULL,
    to_lat          REAL    NOT NULL,
    to_lon          REAL    NOT NULL,
    distance_km     REAL    NOT NULL,
    estimated_time_min INTEGER NOT NULL,
    status          TEXT    NOT NULL CHECK (status IN ('clear', 'blocked', 'partial')) DEFAULT 'clear',
    road_type       TEXT    NOT NULL DEFAULT 'primary',
    notes           TEXT
);

-- Incoming field reports (populated at runtime by Node A submissions)
CREATE TABLE IF NOT EXISTS field_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id     TEXT    NOT NULL,
    timestamp       TEXT    NOT NULL DEFAULT (datetime('now')),
    latitude        REAL,
    longitude       REAL,
    audio_transcript TEXT,
    image_analysis  TEXT,
    threat_level    TEXT    CHECK (threat_level IN ('low', 'moderate', 'high', 'critical')),
    category        TEXT,
    raw_payload     TEXT,   -- full JSON payload from field node
    processed       INTEGER NOT NULL DEFAULT 0
);

-- Standard Operating Procedures (RAG / FTS5)
CREATE VIRTUAL TABLE IF NOT EXISTS sops USING fts5(
    title,
    content
);
"""


# ─── Mock Data ────────────────────────────────────────────────────────

SAFE_ZONES = [
    # (name, type, lat, lon, capacity, occupancy, status, medical, power, notes)
    ("Cascadia Bay High School", "shelter", 46.2104, -123.8165, 800, 312,
     "operational", 1, 1, "Main civic shelter. Generator-powered. Red Cross on site."),
    ("St. Mary's Regional Hospital", "hospital", 46.2051, -123.8098, 450, 389,
     "limited", 1, 1, "ER at capacity. Accepting critical patients only."),
    ("Harbor Park Staging Area", "staging_area", 46.2189, -123.8234, 1200, 145,
     "operational", 0, 0, "Open-air staging. No power. Supplies arriving via helicopter."),
    ("Firehouse #7 — Downtown", "fire_station", 46.2073, -123.8112, 60, 42,
     "operational", 1, 1, "SAR command post. Limited civilian intake."),
    ("Oceanview Community Center", "shelter", 46.2220, -123.8300, 500, 67,
     "operational", 0, 1, "Secondary shelter. Cots and blankets available."),
    ("Cascadia Bay Veterans Hall", "shelter", 46.2005, -123.8050, 350, 280,
     "limited", 1, 0, "Generator failed at 14:00. Medical supplies low."),
    ("Pacific Ridge Elementary", "shelter", 46.2150, -123.8400, 400, 55,
     "operational", 0, 1, "Recently opened. Accessible via Route 7 only."),
    ("Bayfront Medical Clinic", "hospital", 46.2130, -123.8180, 120, 118,
     "limited", 1, 1, "Walk-in clinic. Two physicians on duty."),
]

HAZARDS = [
    # (type, severity, lat, lon, radius_m, description, verified)
    ("collapsed_structure", "critical", 46.2060, -123.8130, 200,
     "6-storey parking garage fully collapsed. Possible survivors trapped.", 1),
    ("gas_leak", "high", 46.2085, -123.8145, 150,
     "Ruptured 6-inch gas main on 4th & Harbor. Active leak, ignition risk.", 1),
    ("flood_zone", "moderate", 46.2200, -123.8250, 500,
     "Columbia Creek overflow. 0.5 m standing water on River Road.", 1),
    ("chemical_spill", "critical", 46.2120, -123.8090, 300,
     "Industrial solvent tank breach at Cascadia Chemical. HazMat deployed.", 1),
    ("downed_power_line", "high", 46.2095, -123.8200, 100,
     "Live 12 kV line across Oak Street. Energised — keep 30 m clearance.", 1),
    ("fire", "high", 46.2040, -123.8070, 250,
     "Structure fire in warehouse district. 3 buildings involved. FD on scene.", 0),
    ("landslide", "moderate", 46.2170, -123.8350, 180,
     "Hillside slump blocking Ridge Road. Single lane passable.", 1),
    ("tsunami_risk", "low", 46.2250, -123.8400, 1000,
     "Advisory: Tsunami watch for coastline. No wave detected yet.", 0),
    ("collapsed_structure", "high", 46.2030, -123.8110, 120,
     "Partial collapse of residential block. 3 units affected.", 1),
    ("gas_leak", "moderate", 46.2160, -123.8280, 80,
     "Minor residential gas leak. Utility crew en route.", 0),
]

ROUTES = [
    # (name, from_loc, to_loc, from_lat, from_lon, to_lat, to_lon,
    #  dist_km, time_min, status, road_type, notes)
    ("Route Alpha — Harbor to School Shelter", "Harbor Park Staging",
     "Cascadia Bay High School", 46.2189, -123.8234, 46.2104, -123.8165,
     2.1, 8, "clear", "primary", "Main evacuation corridor. National Guard escorted."),
    ("Route Bravo — Downtown to Hospital", "Firehouse #7",
     "St. Mary's Hospital", 46.2073, -123.8112, 46.2051, -123.8098,
     0.8, 4, "partial", "secondary", "Debris on 3rd St. One lane open."),
    ("Route Charlie — Coast Road North", "Oceanview Center",
     "Pacific Ridge Elementary", 46.2220, -123.8300, 46.2150, -123.8400,
     3.5, 15, "clear", "primary", "Coastal highway. Clear but monitor tsunami advisory."),
    ("Route Delta — Veterans Hall Evacuation", "Veterans Hall",
     "Harbor Park Staging", 46.2005, -123.8050, 46.2189, -123.8234,
     4.2, 20, "blocked", "primary",
     "BLOCKED — Parking garage collapse debris across intersection."),
    ("Route Echo — Ridge Road Bypass", "Pacific Ridge Elementary",
     "Cascadia Bay High School", 46.2150, -123.8400, 46.2104, -123.8165,
     5.8, 25, "partial", "tertiary",
     "Landslide narrows road. Single lane. 4x4 recommended."),
    ("Route Foxtrot — Medical Corridor", "Bayfront Clinic",
     "St. Mary's Hospital", 46.2130, -123.8180, 46.2051, -123.8098,
     1.5, 6, "clear", "secondary", "Ambulance-priority route. Civilian traffic restricted."),
    ("Route Golf — Industrial Bypass", "Harbor Park Staging",
     "Veterans Hall", 46.2189, -123.8234, 46.2005, -123.8050,
     6.1, 30, "partial", "tertiary",
     "Bypasses collapsed garage via industrial park. Chemical spill nearby — masks required."),
]

SOPS = [
    ("HazMat Response Protocol", "If a chemical spill or hazardous material is detected, all unequipped personnel must maintain a 300m clearance. Use Route Golf for industrial bypass but only with respirator masks. Primary hazmat staging is Firehouse #7."),
    ("Tsunami Evacuation Guidelines", "In the event of a tsunami risk, immediately evacuate all personnel and civilians to ground at least 30m above sea level. Coastal routes like Route Charlie must be cleared and monitored. Direct evacuees to inland shelters such as Pacific Ridge Elementary."),
    ("Structural Collapse Triage", "For collapsed structures, establish a 150m perimeter to prevent secondary collapse casualties. If gas leaks are detected nearby (e.g. within 200m), prohibit all spark-producing equipment. Dispatch Urban SAR teams immediately."),
]


def create_database(db_path: Path, *, reset: bool = False) -> None:
    """Create and populate the local GIS database.

    Parameters
    ----------
    db_path : Path
        Filesystem path for the SQLite database file.
    reset : bool
        If ``True``, drop all tables and recreate from scratch.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    if reset:
        print("[setup_db] Dropping existing tables …")
        for table in ("field_reports", "routes", "hazards", "safe_zones", "sops"):
            cursor.execute(f"DROP TABLE IF EXISTS {table}")

    # ── Create schema ─────────────────────────────────────────────
    print("[setup_db] Creating schema …")
    cursor.executescript(SCHEMA_SQL)

    # ── Populate safe_zones ───────────────────────────────────────
    cursor.execute("SELECT COUNT(*) FROM safe_zones")
    if cursor.fetchone()[0] == 0:
        print(f"[setup_db] Inserting {len(SAFE_ZONES)} safe zones …")
        cursor.executemany(
            """INSERT INTO safe_zones
               (name, type, latitude, longitude, capacity,
                current_occupancy, status, has_medical, has_power, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            SAFE_ZONES,
        )
    else:
        print("[setup_db] safe_zones already populated — skipping.")

    # ── Populate hazards ──────────────────────────────────────────
    cursor.execute("SELECT COUNT(*) FROM hazards")
    if cursor.fetchone()[0] == 0:
        print(f"[setup_db] Inserting {len(HAZARDS)} hazards …")
        cursor.executemany(
            """INSERT INTO hazards
               (type, severity, latitude, longitude, radius_m,
                description, verified)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            HAZARDS,
        )
    else:
        print("[setup_db] hazards already populated — skipping.")

    # ── Populate routes ───────────────────────────────────────────
    cursor.execute("SELECT COUNT(*) FROM routes")
    if cursor.fetchone()[0] == 0:
        print(f"[setup_db] Inserting {len(ROUTES)} routes …")
        cursor.executemany(
            """INSERT INTO routes
               (name, from_location, to_location, from_lat, from_lon,
                to_lat, to_lon, distance_km, estimated_time_min,
                status, road_type, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ROUTES,
        )
    else:
        print("[setup_db] routes already populated — skipping.")

    # ── Populate SOPs ───────────────────────────────────────────
    cursor.execute("SELECT COUNT(*) FROM sops")
    if cursor.fetchone()[0] == 0:
        print(f"[setup_db] Inserting {len(SOPS)} SOPs …")
        cursor.executemany("INSERT INTO sops (title, content) VALUES (?, ?)", SOPS)
    else:
        print("[setup_db] sops already populated — skipping.")

    conn.commit()
    conn.close()
    print("[setup_db] [OK] Database ready at: %s" % db_path)


def print_summary(db_path: Path) -> None:
    """Print a quick summary of database contents."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    print("\n" + "=" * 60)
    print("  AEGIS LOCAL GIS DATABASE - Summary")
    print("=" * 60)

    for table in ("safe_zones", "hazards", "routes", "field_reports", "sops"):
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"  {table:20s}  {count:>4d} records")

    # Show safe zone capacities
    print("\n  -- Safe Zone Capacity --")
    cursor.execute(
        "SELECT name, capacity, current_occupancy, status FROM safe_zones ORDER BY capacity DESC"
    )
    for name, cap, occ, status in cursor.fetchall():
        pct = int(20 * occ / cap)
        bar = "#" * pct + "." * (20 - pct)
        print(f"  {name:35s}  [{bar}]  {occ}/{cap}  [{status}]")

    # Show hazard severity breakdown
    print("\n  -- Hazard Severity --")
    cursor.execute(
        "SELECT severity, COUNT(*) FROM hazards GROUP BY severity ORDER BY "
        "CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 "
        "WHEN 'moderate' THEN 3 WHEN 'low' THEN 4 END"
    )
    sev_markers = {"critical": "!!!", "high": "!! ", "moderate": "!  ", "low": "   "}
    for severity, count in cursor.fetchall():
        marker = sev_markers.get(severity, "   ")
        print(f"  {severity:12s}  {marker} x {count}")

    # Show route statuses
    print("\n  -- Evacuation Routes --")
    cursor.execute("SELECT name, status, estimated_time_min, notes FROM routes ORDER BY status")
    for name, status, time_min, notes in cursor.fetchall():
        icon = {"clear": "[OK]", "partial": "[!!]", "blocked": "[XX]"}.get(status, "[??]")
        print(f"  {icon} {name:45s}  ~{time_min} min  [{status}]")

    print("=" * 60 + "\n")
    conn.close()


# ─── Entry Point ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aegis — Generate mock GIS database for disaster response."
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Drop and recreate all tables (destroys existing data)."
    )
    parser.add_argument(
        "--db-path", type=Path, default=DATABASE_PATH,
        help=f"Database file path (default: {DATABASE_PATH})"
    )
    args = parser.parse_args()

    create_database(args.db_path, reset=args.reset)
    print_summary(args.db_path)


if __name__ == "__main__":
    main()
