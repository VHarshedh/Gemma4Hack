from fastapi.testclient import TestClient
from command_node import create_app
import pytest

app = create_app(use_mock=True)
client = TestClient(app)

def test_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_receive_field_report():
    payload = {
        "report_id": "test-123",
        "operator_id": "TEST-OP",
        "timestamp": "2026-05-14T10:00:00Z",
        "location": {"latitude": 46.2088, "longitude": -123.8156},
        "audio_transcript": "There is a gas leak here.",
        "image_analysis": "Visible ruptured pipe.",
        "threat_level": "critical",
        "category": "gas_leak"
    }
    response = client.post("/api/v1/field-report", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["report_id"] == "test-123"
    assert "dispatch_plan" in data
    assert "status" in data

def test_receive_sensor_data():
    payload = {
        "sensor_id": "TEST-AQ-1",
        "latitude": 46.21,
        "longitude": -123.82,
        "type": "air_quality",
        "value": 150.0,
        "unit": "AQI"
    }
    response = client.post("/api/v1/sensor-data", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

def test_voice_command():
    payload = {"text": "what is the hospital capacity"}
    response = client.post("/api/v1/voice-command", json=payload)
    assert response.status_code == 200
    assert "Cascadia" in response.json()["response"]

def test_portal():
    response = client.get("/portal")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_websocket():
    with client.websocket_connect("/api/v1/ws") as websocket:
        # Test connection succeeds
        assert websocket is not None
