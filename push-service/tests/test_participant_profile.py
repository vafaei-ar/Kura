import json
from unittest.mock import AsyncMock

from app import main
from app.db import ParticipantAccess, PatientContact, EnrollmentAudit
from app.models import CommunicationPreferences
from app.config import Settings, get_settings
from sqlalchemy import select, create_engine, text, inspect
from app.db import _ensure_columns
from tests.test_api import _make_env


def enroll(client, user_id, role="survivor", patient_id="patient-a"):
    response = client.post("/v1/enrollments", json={"user_id": user_id, "role": role,
        "patient_id": patient_id, "caregiver_consent": role == "caregiver"})
    assert response.status_code == 200
    token = client.post("/v1/enrollments/redeem", json={"code": response.json()["enrollment_code"]}).json()["token"]
    return {"Authorization": "Bearer " + token}


def ready(client, user_id, readiness="ready"):
    version = client.get(f"/v1/enrollments/{user_id}/profile").json()["version"]
    result = client.put(f"/v1/enrollments/{user_id}/readiness", json={"expected_version": version,
        "readiness": readiness, "reassess_on": "2026-10-01", "note": "Synthetic agreed support plan"})
    assert result.status_code == 200
    return result.json()


def handoff(client, source, target, version=0, agreement=True):
    return client.post(f"/v1/enrollments/{source}/handoff", json={"target_user_id": target,
        "expected_contact_version": version, "agreement_confirmed": agreement,
        "note": "Synthetic respondent agreement; usual caregiver permission unchanged"})


def test_preferences_are_scoped_validated_versioned_and_do_not_change_permissions():
    client, factory = _make_env()
    first = enroll(client, "first", role="caregiver")
    second = enroll(client, "second")
    assert client.get("/v1/participants/me/preferences").status_code == 401
    initial = client.get("/v1/participants/me/preferences", headers=first).json()
    prefs = {**initial["preferences"], "text_only": True, "speech_rate": 0.65,
             "communication_difficulty": "yes", "support_preference": "staff"}
    body = {"expected_version": 0, "preferences": prefs}
    saved = client.put("/v1/participants/me/preferences", headers=first, json=body)
    assert saved.status_code == 200 and saved.json()["version"] == 1
    assert client.put("/v1/participants/me/preferences", headers=first, json=body).status_code == 409
    assert not client.get("/v1/participants/me/preferences", headers=second).json()["preferences"]["text_only"]
    assert client.put("/v1/participants/me/preferences", headers=first,
        json={**body, "user_id": "second"}).status_code == 422
    assert client.put("/v1/participants/me/preferences", headers=first,
        json={**body, "preferences": {**prefs, "speech_rate": 8}}).status_code == 422
    assert client.put("/v1/participants/me/preferences", headers=first,
        json={**body, "preferences": {**prefs, "audio_consent": True}}).status_code == 422
    with factory() as db:
        row = db.get(ParticipantAccess, "first")
        assert row.role == "caregiver" and row.caregiver_consent and row.readiness == "not_reviewed"
        events = db.execute(select(EnrollmentAudit)).scalars().all()
        assert len(events) == 1 and events[0].actor == "respondent:first"
        assert "token" not in events[0].detail_json


def test_provider_profile_readiness_and_contact_endpoints_reject_participant_credentials():
    client, _ = _make_env(provider_api_key="synthetic-provider")
    provider = {"X-Provider-Key": "synthetic-provider"}
    response = client.post("/v1/enrollments", headers=provider, json={"user_id": "private"})
    code = response.json()["enrollment_code"]
    token = client.post("/v1/enrollments/redeem", json={"code": code}).json()["token"]
    participant = {"Authorization": "Bearer " + token}
    assert client.get("/v1/enrollments", headers=participant).status_code == 401
    assert client.get("/v1/enrollments/private/profile", headers=participant).status_code == 401
    assert client.put("/v1/enrollments/private/readiness", headers=participant,
        json={"expected_version": 0, "readiness": "ready"}).status_code == 401
    assert client.post("/v1/enrollments/private/handoff", headers=participant,
        json={"expected_contact_version": 0, "target_user_id": "private", "agreement_confirmed": True, "note": "not authorized"}).status_code == 401


def test_handoff_requires_same_patient_readiness_permission_and_agreement():
    client, factory = _make_env()
    enroll(client, "caregiver", role="caregiver")
    enroll(client, "survivor")
    enroll(client, "unrelated", patient_id="patient-b")
    enroll(client, "unlinked", patient_id=None)
    assert handoff(client, "caregiver", "survivor").status_code == 409
    assert client.put("/v1/enrollments/survivor/readiness", json={"expected_version": 0, "readiness": "ready"}).status_code == 422
    ready(client, "survivor")
    assert handoff(client, "caregiver", "survivor", agreement=False).status_code == 422
    assert handoff(client, "caregiver", "unrelated").status_code == 422
    assert handoff(client, "unlinked", "unlinked").status_code == 422
    assert handoff(client, "caregiver", "survivor").status_code == 200
    assert handoff(client, "caregiver", "survivor").status_code == 409
    with factory() as db:
        assert db.get(PatientContact, "patient-a").preferred_user_id == "survivor"
        caregiver = db.get(ParticipantAccess, "caregiver")
        assert caregiver.revoked_at is None and caregiver.role == "caregiver" and caregiver.caregiver_consent
        assert len(db.execute(select(EnrollmentAudit).where(EnrollmentAudit.action == "preferred_contact_changed")).scalars().all()) == 2


def test_preferred_invitation_actually_reaches_survivor_with_own_preferences(monkeypatch):
    client, factory = _make_env()
    cg = enroll(client, "caregiver", role="caregiver")
    survivor = enroll(client, "survivor")
    for user_id, credentials in (("caregiver", cg), ("survivor", survivor)):
        client.post("/v1/devices/register", headers=credentials,
                    json={"user_id": user_id, "push_token": "synthetic-" + user_id})
    prefs = CommunicationPreferences(speech_rate=0.7, support_preference="independent")
    assert client.put("/v1/participants/me/preferences", headers=survivor,
        json={"expected_version": 0, "preferences": prefs.model_dump()}).status_code == 200
    ready(client, "survivor")
    assert handoff(client, "caregiver", "survivor").status_code == 200
    start = AsyncMock(return_value="preferred-session")
    monkeypatch.setattr(main.VeraClient, "start_session", start)
    response = client.post("/v1/checkins/start", json={"user_id": "caregiver", "use_preferred_contact": True})
    assert response.status_code == 200 and response.json()["user_id"] == "survivor"
    args = start.await_args.kwargs
    assert args["role"] == "survivor" and args["patient_id"] == "patient-a" and args["rate"] == 0.7
    assert args["communication_preferences"] == prefs.model_dump()
    assert client.get("/v1/checkins/pending/survivor", headers=survivor).json()["invite"]["session_id"] == "preferred-session"
    assert client.get("/v1/checkins/pending/caregiver", headers=cg).json()["invite"] is None
    assert client.get("/v1/checkins/preferred-session/connection", headers=cg).status_code == 403
    ready(client, "survivor", "paused")
    assert client.post("/v1/checkins/start", json={"user_id": "caregiver", "use_preferred_contact": True}).status_code == 409
    assert client.post("/v1/checkins/start", json={"user_id": "survivor"}).status_code == 409
    assert start.await_count == 1


def test_revocation_disables_preferred_route_and_profile_changes():
    client, _ = _make_env()
    enroll(client, "caregiver", role="caregiver")
    credentials = enroll(client, "survivor")
    ready(client, "survivor", "with_support")
    assert handoff(client, "caregiver", "survivor").status_code == 200
    client.post("/v1/enrollments/survivor/revoke")
    assert client.get("/v1/participants/me/preferences", headers=credentials).status_code == 401
    assert client.post("/v1/checkins/start", json={"user_id": "caregiver", "use_preferred_contact": True}).status_code == 403
    assert client.put("/v1/enrollments/survivor/readiness", json={"expected_version": 1, "readiness": "ready"}).status_code == 403


def test_forward_migration_keeps_existing_profile_and_is_repeatable():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE participant_access (user_id VARCHAR(128) PRIMARY KEY, readiness_note TEXT)"))
        connection.execute(text("INSERT INTO participant_access (user_id, readiness_note) VALUES ('existing', 'preserve this')"))
    _ensure_columns(engine)
    _ensure_columns(engine)
    assert {"preferences_json", "profile_version", "readiness", "reassess_on"} <= {c["name"] for c in inspect(engine).get_columns("participant_access")}
    with engine.connect() as connection:
        row = connection.execute(text("SELECT * FROM participant_access")).mappings().one()
        assert row["readiness_note"] == "preserve this" and row["readiness"] == "not_reviewed"
        assert json.loads(row["preferences_json"]) == {}


def test_production_requires_explicit_readiness_without_a_fixed_waiting_period():
    client, _ = _make_env()
    credentials = enroll(client, "solo", patient_id=None)
    client.post("/v1/devices/register", headers=credentials, json={"user_id": "solo", "push_token": "synthetic"})
    main.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None,
        deployment_mode="production", provider_api_key="synthetic-provider", vera_api_base="")
    provider = {"X-Provider-Key": "synthetic-provider"}
    assert client.post("/v1/checkins/start", headers=provider, json={"user_id": "solo"}).status_code == 409
    result = client.put("/v1/enrollments/solo/readiness", headers=provider,
        json={"expected_version": 0, "readiness": "ready", "note": "Synthetic independent-use agreement"})
    assert result.status_code == 200
    assert client.post("/v1/checkins/start", headers=provider, json={"user_id": "solo"}).status_code == 200
