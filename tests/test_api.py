"""
Aegis — FastAPI endpoint tests.
Uses the session-scoped DB fixture from conftest.py (autouse) so the
database is always seeded before the app is constructed here.
"""
from fastapi.testclient import TestClient
from server.app import create_app

app = create_app(use_mock=True)
client = TestClient(app)


def test_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert "model_loaded" in data


def test_receive_field_report():
    payload = {
        "report_id": "test-123",
        "operator_id": "TEST-OP",
        "timestamp": "2026-05-14T10:00:00Z",
        "location": {"latitude": 46.2088, "longitude": -123.8156},
        "audio_transcript": "There is a gas leak here.",
        "image_analysis": "Visible ruptured pipe.",
        "threat_level": "critical",
        "category": "gas_leak",
    }
    response = client.post("/api/v1/field-report", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["report_id"] == "test-123"
    assert data["status"] == "success"
    assert "dispatch_plan" in data
    assert "agent_assessments" in data
    assert len(data["agent_assessments"]) == 3
    agents = {a["agent"] for a in data["agent_assessments"]}
    assert agents == {"hazmat", "logistics", "medical"}


def test_receive_sensor_data_below_threshold():
    payload = {
        "sensor_id": "TEST-AQ-1",
        "latitude": 46.21,
        "longitude": -123.82,
        "type": "air_quality",
        "value": 150.0,
        "unit": "AQI",
    }
    response = client.post("/api/v1/sensor-data", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_receive_sensor_data_critical_threshold():
    """A reading above the critical threshold should return an alert."""
    payload = {
        "sensor_id": "TEST-AQ-2",
        "latitude": 46.21,
        "longitude": -123.82,
        "type": "air_quality",
        "value": 350.0,
        "unit": "AQI",
    }
    response = client.post("/api/v1/sensor-data", json=payload)
    assert response.status_code == 200


def test_voice_command():
    payload = {"text": "what is the hospital capacity"}
    response = client.post("/api/v1/voice-command", json=payload)
    assert response.status_code == 200
    assert "Cascadia" in response.json()["response"]


def test_voice_command_route_query():
    payload = {"text": "which routes are blocked"}
    response = client.post("/api/v1/voice-command", json=payload)
    assert response.status_code == 200
    assert "response" in response.json()


def test_events_endpoint():
    response = client.get("/api/v1/events")
    assert response.status_code == 200
    assert "events" in response.json()


def test_safe_zones_endpoint():
    response = client.get("/api/v1/safe-zones", params={"lat": 46.21, "lon": -123.82, "radius": 10.0})
    assert response.status_code == 200
    zones = response.json()
    assert isinstance(zones, list)
    assert len(zones) > 0
    assert "remaining_capacity" in zones[0]


def test_hazards_endpoint():
    response = client.get("/api/v1/hazards", params={"lat": 46.21, "lon": -123.82, "radius": 10.0})
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_routes_endpoint():
    response = client.get("/api/v1/routes", params={"from_lat": 46.21, "from_lon": -123.82})
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_portal():
    response = client.get("/portal")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_websocket_receives_data():
    """WebSocket connection should accept and be able to receive a broadcast."""
    with client.websocket_connect("/api/v1/ws") as ws:
        # Trigger a broadcast by posting a sensor reading, then check WS got it
        client.post("/api/v1/sensor-data", json={
            "sensor_id": "WS-TEST-1",
            "latitude": 46.21,
            "longitude": -123.82,
            "type": "flood",
            "value": 0.5,
            "unit": "m",
        })
        data = ws.receive_json()
        assert "msg_type" in data
        assert data["msg_type"] == "sensor"
