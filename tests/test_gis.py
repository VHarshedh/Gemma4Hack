"""
Aegis — GIS database unit tests.
The session-scoped DB fixture in conftest.py seeds the database
before these tests run.
"""
import pytest
from core.gis_sqlite import GISDatabase, haversine
from config import DATABASE_PATH


@pytest.fixture(scope="module")
def db():
    """Return a GISDatabase pointed at the already-seeded test DB."""
    return GISDatabase(DATABASE_PATH)


# ── Haversine ────────────────────────────────────────────────────────

def test_haversine_same_point():
    assert haversine(46.0, -123.0, 46.0, -123.0) == pytest.approx(0.0)


def test_haversine_one_degree_latitude():
    dist = haversine(46.0, -123.0, 47.0, -123.0)
    assert 110 < dist < 112


def test_haversine_symmetry():
    a = haversine(46.0, -123.0, 46.5, -123.5)
    b = haversine(46.5, -123.5, 46.0, -123.0)
    assert a == pytest.approx(b)


# ── Safe Zones ───────────────────────────────────────────────────────

def test_query_safe_zones_returns_results(db):
    zones = db.query_safe_zones(46.2104, -123.8165, radius_km=5.0, zone_type="any")
    assert len(zones) > 0


def test_query_safe_zones_has_remaining_capacity(db):
    zones = db.query_safe_zones(46.2104, -123.8165, radius_km=5.0, zone_type="any")
    assert "remaining_capacity" in zones[0]
    assert zones[0]["remaining_capacity"] >= 0


def test_query_safe_zones_sorted_by_distance(db):
    zones = db.query_safe_zones(46.2104, -123.8165, radius_km=5.0, zone_type="any")
    distances = [z["distance_km"] for z in zones]
    assert distances == sorted(distances)


def test_query_safe_zones_type_filter(db):
    hospitals = db.query_safe_zones(46.2104, -123.8165, radius_km=10.0, zone_type="hospital")
    for h in hospitals:
        assert h["type"] == "hospital"


def test_query_safe_zones_excludes_offline(db):
    zones = db.query_safe_zones(46.2104, -123.8165, radius_km=10.0, zone_type="any")
    for z in zones:
        assert z["status"] != "offline"


# ── Hazards ──────────────────────────────────────────────────────────

def test_query_hazards_returns_results(db):
    hazards = db.query_hazards(46.2060, -123.8130, radius_km=2.0, min_severity="moderate")
    assert len(hazards) > 0


def test_query_hazards_severity_filter(db):
    hazards = db.query_hazards(46.2060, -123.8130, radius_km=2.0, min_severity="moderate")
    for h in hazards:
        assert h["severity"] in ("moderate", "high", "critical")


def test_query_hazards_low_severity_includes_all(db):
    all_hazards = db.query_hazards(46.2060, -123.8130, radius_km=5.0, min_severity="low")
    moderate_plus = db.query_hazards(46.2060, -123.8130, radius_km=5.0, min_severity="moderate")
    assert len(all_hazards) >= len(moderate_plus)


# ── Routes ───────────────────────────────────────────────────────────

def test_query_routes_returns_results(db):
    routes = db.query_routes(from_lat=46.2088, from_lon=-123.8156)
    assert len(routes) > 0


def test_query_routes_has_expected_fields(db):
    routes = db.query_routes(from_lat=46.2088, from_lon=-123.8156)
    for r in routes:
        assert "name" in r
        assert "status" in r
        assert "distance_km" in r
        assert "estimated_time_min" in r
        assert "proximity_to_origin_km" in r


def test_query_routes_status_filter(db):
    clear_routes = db.query_routes(from_lat=46.2088, from_lon=-123.8156, status_filter="clear")
    for r in clear_routes:
        assert r["status"] == "clear"


# ── SOPs ─────────────────────────────────────────────────────────────

def test_query_sop_returns_results(db):
    sops = db.query_sop("chemical spill")
    assert len(sops) > 0


def test_query_sop_hazmat_content(db):
    sops = db.query_sop("chemical spill")
    titles = [s["title"] for s in sops]
    assert any("HazMat" in t for t in titles)


def test_query_sop_has_content_field(db):
    sops = db.query_sop("gas leak")
    for s in sops:
        assert "title" in s
        assert "content" in s
        assert len(s["content"]) > 0


# ── Write Operations ─────────────────────────────────────────────────

def test_store_field_report(db):
    report = {
        "operator_id": "TEST-OP",
        "timestamp": "2026-05-14T10:00:00Z",
        "location": {"latitude": 46.2088, "longitude": -123.8156},
        "audio_transcript": "Gas leak confirmed.",
        "image_analysis": "Ruptured pipe visible.",
        "threat_level": "critical",
        "category": "gas_leak",
    }
    row_id = db.store_field_report(report)
    assert isinstance(row_id, int)
    assert row_id > 0


# ── Tool Dispatcher ──────────────────────────────────────────────────

def test_execute_tool_safe_zones(db):
    result = db.execute_tool("query_safe_zones", {"latitude": 46.2088, "longitude": -123.8156})
    assert isinstance(result, list)


def test_execute_tool_hazards(db):
    result = db.execute_tool("query_hazards", {"latitude": 46.2088, "longitude": -123.8156})
    assert isinstance(result, list)


def test_execute_tool_routes(db):
    result = db.execute_tool("query_routes", {"from_lat": 46.2088, "from_lon": -123.8156})
    assert isinstance(result, list)


def test_execute_tool_sop(db):
    result = db.execute_tool("query_sop", {"query": "gas leak"})
    assert isinstance(result, list)


def test_execute_tool_unknown_returns_error(db):
    result = db.execute_tool("nonexistent_tool", {})
    assert isinstance(result, dict)
    assert "error" in result
