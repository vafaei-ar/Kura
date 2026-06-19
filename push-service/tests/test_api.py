"""End-to-end tests for the push-service in DRY_RUN mode (no Apple/VERA needed)."""
from fastapi.testclient import TestClient

from app.main import app, get_store
from app.store import DeviceStore


def fresh_client() -> TestClient:
    # Isolate each test with its own in-memory store, but reuse the SAME
    # instance across requests within the test (one store per client).
    store = DeviceStore(":memory:")
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app)


def test_health_ok():
    client = fresh_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["dry_run"] is True


def test_register_then_start_checkin():
    client = fresh_client()

    reg = client.post(
        "/v1/devices/register",
        json={"user_id": "patient-001", "push_token": "abcdef0123456789"},
    )
    assert reg.status_code == 200
    assert reg.json()["token_type"] == "alert"

    start = client.post(
        "/v1/checkins/start",
        json={"user_id": "patient-001", "scenario": "guided.yml"},
    )
    assert start.status_code == 200
    body = start.json()
    assert body["user_id"] == "patient-001"
    assert body["push_sent"] is True
    assert body["push_dry_run"] is True
    assert body["session_id"]  # a stubbed UUID when VERA is not configured


def test_start_checkin_unknown_user_404():
    client = fresh_client()
    r = client.post("/v1/checkins/start", json={"user_id": "nope"})
    assert r.status_code == 404


def test_device_token_not_leaked():
    client = fresh_client()
    client.post(
        "/v1/devices/register",
        json={"user_id": "p2", "push_token": "0123456789abcdef0123456789"},
    )
    r = client.get("/v1/devices/p2")
    assert r.status_code == 200
    assert r.json()["token_preview"].endswith("…")
    assert "push_token" not in r.json()


def test_mock_audio_ws_sends_ready():
    client = fresh_client()
    with client.websocket_connect("/ws/audio/sess-123") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "ready"
        assert msg["session_id"] == "sess-123"


def test_register_is_idempotent_upsert():
    client = fresh_client()
    a = client.post(
        "/v1/devices/register",
        json={"user_id": "p3", "push_token": "tokenA00000000000"},
    )
    b = client.post(
        "/v1/devices/register",
        json={"user_id": "p3", "push_token": "tokenB11111111111"},
    )
    assert a.status_code == b.status_code == 200
    # registered_at preserved, token updated
    assert b.json()["push_token"] == "tokenB11111111111"
