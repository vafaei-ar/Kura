from app import main
from app.db import ParticipantAccess
from app.config import Settings, get_settings
from app.db import Checkin
from unittest.mock import AsyncMock
from tests.test_api import _make_env


def enroll(client, user_id="caregiver1"):
    body = {"user_id": user_id, "patient_id": "patient1", "role": "caregiver", "caregiver_consent": True}
    response = client.post("/v1/enrollments", json=body)
    assert response.status_code == 200
    code = response.json()["enrollment_code"]
    token = client.post("/v1/enrollments/redeem", json={"code": code}).json()["token"]
    return code, token


def test_one_use_code_hashed_tokens_and_cannot_choose_a_different_role():
    client, factory = _make_env()
    code, token = enroll(client)
    assert client.post("/v1/enrollments/redeem", json={"code": code}).status_code == 401
    with factory() as db:
        row = db.get(ParticipantAccess, "caregiver1")
        assert row.code_hash is None and row.token_hash != token and len(row.token_hash) == 64
    body = {"user_id": "caregiver1", "push_token": "synthetic", "role": "survivor"}
    assert client.post("/v1/devices/register", json=body).status_code == 401
    result = client.post("/v1/devices/register", json=body, headers={"Authorization": "Bearer " + token})
    assert result.json()["role"] == "caregiver"


def test_cross_account_and_revoked_credentials_are_rejected():
    client, _ = _make_env()
    _, token = enroll(client)
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/v1/checkins/pending/someoneelse", headers=headers).status_code == 403
    assert client.get("/v1/checkins/pending/caregiver1", headers=headers).status_code == 200
    assert client.post("/v1/enrollments/caregiver1/revoke").status_code == 200
    assert client.get("/v1/checkins/pending/caregiver1", headers=headers).status_code == 401
    assert client.get("/v1/checkins/pending/caregiver1").status_code == 401


def test_caregiver_link_requires_authorized_permission():
    client, _ = _make_env()
    assert client.post("/v1/enrollments", json={"user_id": "c", "patient_id": "p", "role": "caregiver"}).status_code == 422


def test_production_participant_endpoints_do_not_allow_demo_fallback():
    client, _ = _make_env()
    main.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, deployment_mode="production", provider_api_key="provider-test", vera_api_base="")
    assert client.get("/v1/checkins/pending/unknown").status_code == 401
    assert client.post("/v1/devices/register", json={"user_id": "unknown", "push_token": "synthetic"}).status_code == 401
    assert client.post("/v1/enrollments", json={"user_id": "new"}).status_code == 401


def test_reissue_preserves_link_and_invalidates_previous_bearer():
    client, factory = _make_env()
    _, token = enroll(client)
    code = client.post("/v1/enrollments/caregiver1/reissue").json()["enrollment_code"]
    assert client.get("/v1/checkins/pending/caregiver1", headers={"Authorization": "Bearer " + token}).status_code == 401
    assert client.post("/v1/enrollments/redeem", json={"code": code}).status_code == 200
    with factory() as db:
        assert db.get(ParticipantAccess, "caregiver1").patient_id == "patient1"


def test_audio_upload_broker_requires_owner_and_forwards_without_clinical_work(monkeypatch):
    client, factory = _make_env()
    _, token = enroll(client)
    with factory() as db:
        db.add(Checkin(session_id="audio-test", user_id="caregiver1", scenario="guided.yml", role="caregiver", status="escalated"))
        db.commit()
    upload = AsyncMock(return_value=(200, {"stored": True, "clip_id": "turn"}))
    monkeypatch.setattr(main.VeraClient, "upload_audio", upload)
    path = "/v1/checkins/audio-test/audio/turn"
    assert client.post(path, content=b"synthetic").status_code == 401
    response = client.post(path, headers={"Authorization": "Bearer " + token}, content=b"synthetic")
    assert response.json()["stored"]
    upload.assert_awaited_once_with("audio-test", "turn", b"synthetic", False)
    client.post("/v1/enrollments/caregiver1/revoke")
    assert client.post(path, headers={"Authorization": "Bearer " + token}, content=b"synthetic").status_code == 401
