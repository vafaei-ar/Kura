from unittest.mock import AsyncMock

from app import main
from app.db import Checkin
from tests.test_api import _make_env
from tests.test_participant_auth import enroll


def test_recovery_receipt_requires_owner_and_preserves_terminal_status(monkeypatch):
    client, factory = _make_env()
    _, token = enroll(client)
    _, other = enroll(client, "someoneelse")
    with factory() as db:
        db.add(Checkin(session_id="recovery", user_id="caregiver1", scenario="guided.yml", role="caregiver", status="escalated"))
        db.commit()
    result = {"message_id": "turn", "saved": True, "status": "accepted", "state": "escalated", "can_retry": False}
    remote = AsyncMock(return_value=result)
    monkeypatch.setattr(main.VeraClient, "answer_receipt", remote)
    path = "/v1/checkins/recovery/answer-receipt"
    assert client.post(path, json={}).status_code == 401
    assert client.post(path, json={}, headers={"Authorization": "Bearer " + other}).status_code == 403
    assert remote.await_count == 0
    response = client.post(path, json={"message_id": "turn"}, headers={"Authorization": "Bearer " + token})
    assert response.json() == result and response.headers["cache-control"] == "no-store"
    with factory() as db:
        row = db.get(Checkin, "recovery")
        row.needs_revoke = True
        db.commit()
    assert client.post(path, json={}, headers={"Authorization": "Bearer " + token}).status_code == 403
    assert remote.await_count == 1


def test_receipt_outage_is_not_reported_as_missing_or_saved(monkeypatch):
    client, factory = _make_env()
    _, token = enroll(client)
    with factory() as db:
        db.add(Checkin(session_id="outage", user_id="caregiver1", scenario="guided.yml", role="caregiver", status="started"))
        db.commit()
    monkeypatch.setattr(main.VeraClient, "answer_receipt", AsyncMock(side_effect=RuntimeError("synthetic outage")))
    response = client.post("/v1/checkins/outage/answer-receipt", json={}, headers={"Authorization": "Bearer " + token})
    assert response.status_code == 503
