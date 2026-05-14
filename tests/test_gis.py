import pytest
from pathlib import Path
from command_node import GISDatabase, _haversine
from config import DATABASE_PATH
import setup_db

@pytest.fixture(scope="module")
def test_db():
    setup_db.create_database(DATABASE_PATH, reset=True)
    gis = GISDatabase(DATABASE_PATH)
    yield gis

def test_haversine():
    assert _haversine(46.0, -123.0, 46.0, -123.0) == 0.0
    # ~111 km per degree of latitude
    dist = _haversine(46.0, -123.0, 47.0, -123.0)
    assert 110 < dist < 112

def test_query_safe_zones(test_db):
    zones = test_db.query_safe_zones(46.2104, -123.8165, radius_km=5.0, zone_type="any")
    assert len(zones) > 0
    assert "remaining_capacity" in zones[0]

def test_query_hazards(test_db):
    hazards = test_db.query_hazards(46.2060, -123.8130, radius_km=2.0, min_severity="moderate")
    assert len(hazards) > 0
    for h in hazards:
        assert h["severity"] in ["moderate", "high", "critical"]

def test_query_sop(test_db):
    sops = test_db.query_sop("chemical spill")
    assert len(sops) > 0
    assert "HazMat" in sops[0]["title"]
