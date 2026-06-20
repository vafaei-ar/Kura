"""End-to-end tests for the push-service in DRY_RUN mode (no Apple/VERA needed)."""
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import build_engine, make_session_factory
from app.main import app, get_db


def fresh_client() -> TestClient:
    # Each test gets its own fresh in-memory database.
    engine = build_engine("sqlite://")
    SessionLocal = make_session_factory(engine)

    def _get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    # Force stub settings so tests don't read a real .env (VERA_API_BASE, etc.)
    # and don't reach the network. dry_run keeps APNs offline.
    test_settings = Settings(vera_api_base="", provider_api_key="", dry_run=True)
    app.dependency_overrides[get_settings] = lambda: test_settings
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


def test_console_served():
    client = fresh_client()
    r = client.get("/")
    assert r.status_code == 200
    assert "Provider Console" in r.text


def test_list_devices():
    client = fresh_client()
    client.post("/v1/devices/register", json={"user_id": "p1", "push_token": "abc12345xyz"})
    client.post("/v1/devices/register", json={"user_id": "p2", "push_token": "def67890xyz"})
    r = client.get("/v1/devices")
    assert r.status_code == 200
    users = {d["user_id"] for d in r.json()}
    assert users == {"p1", "p2"}
    assert all("push_token" not in d for d in r.json())


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


def test_history_and_summary_not_ready():
    client = fresh_client()
    client.post("/v1/devices/register", json={"user_id": "p8", "push_token": "tok8888888"})
    start = client.post("/v1/checkins/start", json={"user_id": "p8", "scenario": "micro_routine"})
    sid = start.json()["session_id"]
    # history lists the started check-in
    hist = client.get("/v1/checkins").json()
    assert any(h["session_id"] == sid and h["scenario"] == "micro_routine" for h in hist)
    # summary not ready (VERA not configured in tests)
    summ = client.get(f"/v1/checkins/{sid}/summary").json()
    assert summ["ready"] is False


def test_poll_pending_returns_then_clears():
    client = fresh_client()
    client.post("/v1/devices/register", json={"user_id": "p7", "push_token": "tok7777777"})
    # nothing pending yet
    assert client.get("/v1/checkins/pending/p7").json()["invite"] is None
    # provider starts a check-in -> it queues
    start = client.post("/v1/checkins/start", json={"user_id": "p7"})
    sid = start.json()["session_id"]
    # first poll returns the invite
    first = client.get("/v1/checkins/pending/p7").json()["invite"]
    assert first["session_id"] == sid
    assert first["type"] == "checkin_invite"
    # second poll is empty (cleared)
    assert client.get("/v1/checkins/pending/p7").json()["invite"] is None


def test_notify_ws_receives_invite():
    client = fresh_client()
    client.post(
        "/v1/devices/register",
        json={"user_id": "p9", "push_token": "tok000000000000"},
    )
    with client.websocket_connect("/v1/notify/p9") as ws:
        ack = ws.receive_json()
        assert ack["type"] == "connected"
        r = client.post("/v1/checkins/start", json={"user_id": "p9"})
        assert r.status_code == 200
        assert r.json()["live_delivered"] == 1
        invite = ws.receive_json()
        assert invite["type"] == "checkin_invite"
        assert invite["session_id"] == r.json()["session_id"]


def test_mock_audio_ws_conversation():
    client = fresh_client()
    with client.websocket_connect("/ws/audio/sess-123") as ws:
        greeting = ws.receive_json()
        assert greeting["type"] == "greeting"
        # First user turn -> first question
        ws.send_json({"type": "text_input", "text": "I'm okay"})
        q1 = ws.receive_json()
        assert q1["type"] == "response"
        assert q1["progress"] > 0
        # Exhaust the script -> completion
        for _ in range(5):
            ws.send_json({"type": "text_input", "text": "yes"})
            msg = ws.receive_json()
            if msg["type"] == "completion":
                break
        assert msg["type"] == "completion"
        assert msg["progress"] == 100


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
    # token updated (masked preview reflects the new token's prefix)
    assert b.json()["token_preview"].startswith("tokenB1")
