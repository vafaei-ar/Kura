import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

from app import main
from app.config import Settings, get_settings
from app.db import Checkin
from tests.test_api import _make_env


def setup_event():
    client, factory = _make_env()
    main.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, vera_api_base="", provider_api_key="", dry_run=True, vera_event_key="event-test-key")
    client.post("/v1/devices/register", json={"user_id": "feedback", "push_token": "synthetic"})
    sid = client.post("/v1/checkins/start", json={"user_id": "feedback"}).json()["session_id"]
    return client, factory, sid


def event(sid, version, tier=1, urgency=None):
    return {"session_id": sid, "version": version, "summary": {
        "session_id": sid, "version": version, "state": "escalated" if tier == 1 else "completed",
        "priority_items": [{"tier": tier, "rule_id": "test"}] if tier < 3 else [],
        "has_priority": tier < 3 or urgency in ("soon", "urgent"), "user_reported_urgency": urgency,
    }}


def test_event_requires_independent_secret_and_registered_session():
    client, _, sid = setup_event()
    assert client.post("/v1/vera/events", json=event(sid, 1)).status_code == 401
    assert client.post("/v1/vera/events", headers={"X-Event-Key": "event-test-key"}, json=event("missing", 1)).status_code == 409


def test_duplicate_and_out_of_order_events_never_replace_newer_outcome(monkeypatch):
    client, factory, sid = setup_event()
    monkeypatch.setattr(main.notify_email, "send_alert", lambda *a, **k: True)
    headers = {"X-Event-Key": "event-test-key"}
    assert client.post("/v1/vera/events", headers=headers, json=event(sid, 2)).json()["accepted_version"] == 2
    assert client.post("/v1/vera/events", headers=headers, json=event(sid, 1, tier=3)).json()["accepted_version"] == 2
    with factory() as db:
        row = db.get(Checkin, sid)
        assert row.has_priority and row.summary_version == 2
        assert row.alert_attempts == 1 and row.alert_state == "smtp_accepted"


def test_new_concern_reopens_resolved_work_without_duplicate_reopening(monkeypatch):
    client, factory, sid = setup_event()
    monkeypatch.setattr(main.notify_email, "send_alert", lambda *a, **k: True)
    headers = {"X-Event-Key": "event-test-key"}
    client.post("/v1/vera/events", headers=headers, json=event(sid, 1, tier=2))
    client.post(f"/v1/checkins/{sid}/acknowledge", json={"owner": "navigator"})
    client.post(f"/v1/checkins/{sid}/resolve")
    client.post("/v1/vera/events", headers=headers, json=event(sid, 2, tier=2))
    with factory() as db:
        assert db.get(Checkin, sid).resolved_at is not None
    client.post("/v1/vera/events", headers=headers, json=event(sid, 3, tier=1))
    with factory() as db:
        row = db.get(Checkin, sid)
        assert row.resolved_at is None and row.acknowledged_at is None
        assert row.owner == "navigator" and row.alert_state == "smtp_accepted"
    assert any("New concern" in note["text"] for note in client.get(f"/v1/checkins/{sid}/notes").json())


def test_failed_alert_is_retryable_and_not_marked_sent(monkeypatch):
    client, factory, sid = setup_event()
    monkeypatch.setattr(main.notify_email, "send_alert", lambda *a, **k: False)
    result = client.post("/v1/vera/events", headers={"X-Event-Key": "event-test-key"}, json=event(sid, 1))
    assert result.json()["alert_state"] == "failed"
    with factory() as db:
        row = db.get(Checkin, sid)
        assert row.alerted_at is None and row.alert_attempts == 1
        row.alert_next_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        monkeypatch.setattr(main.notify_email, "send_alert", lambda *a, **k: True)
        main._maybe_send_emergency_alert(row, json.loads(row.summary_json), Settings(_env_file=None), db)
        assert row.alerted_at is not None and row.alert_attempts == 2


def test_no_false_completion_and_invalid_urgency_rejected(monkeypatch):
    client, _, sid = setup_event()
    assert client.post(f"/v1/checkins/{sid}/complete").status_code == 503
    assert client.post("/v1/checkins/missing/complete").status_code == 404
    assert client.post(f"/v1/checkins/{sid}/complete", json={"urgency": "banana"}).status_code == 422
    monkeypatch.setattr(main.VeraClient, "record_urgency", AsyncMock(side_effect=RuntimeError("offline")))
    assert client.post(f"/v1/checkins/{sid}/complete", json={"urgency": "soon"}).status_code == 502


def test_summary_refresh_keeps_latest_urgency(monkeypatch):
    client, factory, sid = setup_event()
    old = event(sid, 1, tier=3)["summary"]
    with factory() as db:
        main._store_summary(db.get(Checkin, sid), old, db)
    newer = event(sid, 2, tier=3, urgency="soon")["summary"]
    monkeypatch.setattr(main.VeraClient, "clinician_summary", AsyncMock(return_value=newer))
    assert client.get(f"/v1/checkins/{sid}/summary").json()["summary"]["user_reported_urgency"] == "soon"


def test_preflight_decline_is_not_completion():
    client, factory, sid = setup_event()
    result = client.post(f"/v1/checkins/{sid}/decline")
    assert result.json()["state"] == "declined"
    with factory() as db:
        assert db.get(Checkin, sid).completed_at is None
    assert client.get("/v1/checkins/pending/feedback").json()["invite"] is None


def test_ask_sharing_creates_linked_worklist_record(monkeypatch):
    client, factory, _ = setup_event()
    sid = "ask-request1"
    summary = event(sid, 1, tier=3)["summary"]
    summary.update(callback_requested=True, has_priority=True)
    mock = AsyncMock(return_value={"kind": "refusal", "answer": "Contact the care team.", "saved": True, "summary": summary})
    monkeypatch.setattr(main.VeraClient, "ask", mock)
    result = client.post("/v1/ask", json={"question": "I need help", "share_with_team": True,
        "callback_requested": True, "user_id": "feedback", "request_id": "request1"})
    assert result.status_code == 200 and "not yet confirmed" in result.json()["answer"]
    with factory() as db:
        row = db.get(Checkin, sid)
        assert row.user_id == "feedback" and row.has_priority


def test_unversioned_response_cannot_overwrite_event():
    _, factory, sid = setup_event()
    with factory() as db:
        row = db.get(Checkin, sid)
        main._store_summary(row, event(sid, 3)["summary"], db)
        main._store_summary(row, {"has_priority": False}, db)
        assert row.has_priority and row.summary_version == 3


def test_retry_lease_handles_sqlite_round_trip_datetimes(monkeypatch):
    client, factory, sid = setup_event()
    monkeypatch.setattr(main.notify_email, "send_alert", lambda *a, **k: False)
    client.post("/v1/vera/events", headers={"X-Event-Key": "event-test-key"}, json=event(sid, 1))
    with factory() as db:
        row = db.get(Checkin, sid)  # SQLite returns a naive datetime after reload
        main._maybe_send_emergency_alert(row, json.loads(row.summary_json), Settings(_env_file=None), db)
        assert row.alert_attempts == 1  # future retry lease remains respected


def test_explicit_recovery_discovers_received_but_unfinished_invite():
    client, factory, sid = setup_event()
    client.post(f"/v1/checkins/{sid}/received")
    assert client.get("/v1/checkins/pending/feedback").json()["invite"] is None
    assert client.get("/v1/checkins/pending/feedback?include_received=true").json()["invite"]["session_id"] == sid
    with factory() as db:
        row = db.get(Checkin, sid)
        row.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.commit()
    assert client.get("/v1/checkins/pending/feedback?include_received=true").json()["invite"] is None
