"""End-to-end tests for the push-service in DRY_RUN mode (no Apple/VERA needed)."""
import uuid

from fastapi.testclient import TestClient

from app import auth as auth_lib
from app.config import Settings, get_settings
from app.db import Clinician, build_engine, make_session_factory
from app.main import app, get_db


def _make_env(provider_api_key: str = ""):
    """Fresh in-memory DB + settings. Returns (client, SessionLocal)."""
    engine = build_engine("sqlite://")
    SessionLocal = make_session_factory(engine)

    def _get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    test_settings = Settings(
        vera_api_base="", provider_api_key=provider_api_key, dry_run=True,
        session_secret="unit-test-secret",
    )
    app.dependency_overrides[get_settings] = lambda: test_settings
    return TestClient(app), SessionLocal


def fresh_client() -> TestClient:
    client, _ = _make_env()
    return client


def seed_clinician(SessionLocal, username="doc", password="passw0rd1", role="physician",
                   name="Dr. Test", must_change=False) -> str:
    db = SessionLocal()
    try:
        row = Clinician(
            id=str(uuid.uuid4()), username=username, display_name=name, role=role,
            password_hash=auth_lib.hash_password(password), must_change_password=must_change,
            is_active=True,
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def mark_priority(SessionLocal, session_id):
    from app.db import Checkin
    db = SessionLocal()
    try:
        row = db.get(Checkin, session_id)
        row.has_priority = True
        row.status = "completed"
        db.commit()
    finally:
        db.close()


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


def test_ask_unavailable_without_vera():
    client = fresh_client()
    r = client.post("/v1/ask", json={"question": "what is a tia?"})
    assert r.status_code == 200
    assert r.json()["kind"] == "refusal"  # VERA not configured in tests
    # empty question is handled
    assert client.post("/v1/ask", json={"question": ""}).json()["kind"] == "refusal"


def test_resources_unavailable_without_vera():
    client = fresh_client()
    r = client.get("/v1/resources?need=transportation")
    assert r.status_code == 200
    assert "disclaimer" in r.json()
    regions = client.get("/v1/resource-regions").json()
    assert "transportation" in regions["needs"]


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


def test_delete_checkin_removes_record():
    client = fresh_client()
    client.post("/v1/devices/register", json={"user_id": "pd1", "push_token": "tokdddddddd"})
    sid = client.post("/v1/checkins/start", json={"user_id": "pd1"}).json()["session_id"]
    assert any(h["session_id"] == sid for h in client.get("/v1/checkins").json())
    d = client.delete(f"/v1/checkins/{sid}")
    assert d.status_code == 200 and d.json()["deleted"] == sid
    assert not any(h["session_id"] == sid for h in client.get("/v1/checkins").json())
    # deleting again -> 404
    assert client.delete(f"/v1/checkins/{sid}").status_code == 404


def test_delete_patient_removes_device_and_checkins():
    client = fresh_client()
    client.post("/v1/devices/register", json={"user_id": "pd2", "push_token": "tokeeeeeeee"})
    client.post("/v1/checkins/start", json={"user_id": "pd2"})
    d = client.delete("/v1/devices/pd2")
    assert d.status_code == 200 and d.json()["deleted"] == "pd2"
    # device gone
    assert client.get("/v1/devices/pd2").status_code == 404
    # their check-ins gone too
    assert not any(h["user_id"] == "pd2" for h in client.get("/v1/checkins").json())
    # deleting again -> 404
    assert client.delete("/v1/devices/pd2").status_code == 404


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


# --- Step 3: clinician auth + triage workflow ---------------------------

def test_login_me_and_bad_password():
    client, SL = _make_env()
    seed_clinician(SL, username="jlee", password="goodpass1", name="Dr. Lee", role="physician")
    # wrong password rejected
    assert client.post("/v1/auth/login", json={"username": "jlee", "password": "nope"}).status_code == 401
    # correct password works and sets a session cookie
    r = client.post("/v1/auth/login", json={"username": "jlee", "password": "goodpass1"})
    assert r.status_code == 200
    assert r.json()["clinician"]["display_name"] == "Dr. Lee"
    me = client.get("/v1/auth/me")
    assert me.status_code == 200 and me.json()["clinician"]["role"] == "physician"
    # logout clears the session
    client.post("/v1/auth/logout")
    assert client.get("/v1/auth/me").status_code == 401


def test_protected_endpoint_requires_auth_when_key_set():
    client, SL = _make_env(provider_api_key="shared-secret")
    # no cookie, no key -> 401
    assert client.get("/v1/devices").status_code == 401
    # legacy shared key still works (transition path)
    assert client.get("/v1/devices", headers={"X-Provider-Key": "shared-secret"}).status_code == 200
    # logged-in clinician works without the key
    seed_clinician(SL, username="nav", password="navpass12")
    client.post("/v1/auth/login", json={"username": "nav", "password": "navpass12"})
    assert client.get("/v1/devices").status_code == 200


def test_change_password_first_login():
    client, SL = _make_env()
    seed_clinician(SL, username="newdoc", password="temp12345", must_change=True)
    login = client.post("/v1/auth/login", json={"username": "newdoc", "password": "temp12345"})
    assert login.json()["clinician"]["must_change_password"] is True
    # too-short new password rejected
    assert client.post("/v1/auth/change-password",
                       json={"current_password": "temp12345", "new_password": "short"}).status_code == 400
    # valid change works and clears the flag
    r = client.post("/v1/auth/change-password",
                    json={"current_password": "temp12345", "new_password": "brandNew99"})
    assert r.status_code == 200 and r.json()["clinician"]["must_change_password"] is False
    # old password no longer logs in; new one does
    client.post("/v1/auth/logout")
    assert client.post("/v1/auth/login", json={"username": "newdoc", "password": "temp12345"}).status_code == 401
    assert client.post("/v1/auth/login", json={"username": "newdoc", "password": "brandNew99"}).status_code == 200


def test_acknowledge_resolve_attribution_and_count():
    client, SL = _make_env()
    seed_clinician(SL, username="jlee", password="goodpass1", name="Dr. Lee", role="physician")
    client.post("/v1/auth/login", json={"username": "jlee", "password": "goodpass1"})
    client.post("/v1/devices/register", json={"user_id": "pa1", "push_token": "tok11111111"})
    sid = client.post("/v1/checkins/start", json={"user_id": "pa1", "scenario": "micro_redflag"}).json()["session_id"]
    mark_priority(SL, sid)
    # one open priority item
    assert client.get("/v1/checkins/priority-count").json()["open_priority"] == 1
    # acknowledge records the acting clinician
    ack = client.post(f"/v1/checkins/{sid}/acknowledge").json()
    assert ack["acknowledged_at"] is not None
    assert "Dr. Lee" in ack["acknowledged_by"] and "physician" in ack["acknowledged_by"]
    # resolve closes it (and implies acknowledged)
    res = client.post(f"/v1/checkins/{sid}/resolve").json()
    assert res["resolved_at"] is not None and "Dr. Lee" in res["resolved_by"]
    assert client.get("/v1/checkins/priority-count").json()["open_priority"] == 0
    # unresolved filter now excludes it
    assert not any(h["session_id"] == sid for h in client.get("/v1/checkins?unresolved_priority=true").json())
    # reopen brings it back
    client.post(f"/v1/checkins/{sid}/reopen")
    assert client.get("/v1/checkins/priority-count").json()["open_priority"] == 1


def test_patient_detail_timeline():
    client, SL = _make_env()
    seed_clinician(SL, username="jlee", password="goodpass1")
    client.post("/v1/auth/login", json={"username": "jlee", "password": "goodpass1"})
    client.post("/v1/devices/register",
                json={"user_id": "pt9", "push_token": "tok99999999", "display_name": "Sam"})
    s1 = client.post("/v1/checkins/start", json={"user_id": "pt9"}).json()["session_id"]
    client.post("/v1/checkins/start", json={"user_id": "pt9"})
    mark_priority(SL, s1)
    d = client.get("/v1/patients/pt9").json()
    assert d["patient"]["display_name"] == "Sam"
    assert d["summary"]["total"] == 2
    assert d["summary"]["priority"] == 1 and d["summary"]["open_priority"] == 1
    # unknown patient -> 404
    assert client.get("/v1/patients/nobody").status_code == 404
