"""Communication preferences and explicit care-contact handoff, not clinical triage."""
import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .db import ParticipantAccess, PatientContact, EnrollmentAudit
from .models import CommunicationPreferences


def active_enrollment(db, user_id):
    row = db.get(ParticipantAccess, user_id)
    if row is None:
        raise HTTPException(404, "Unknown enrollment")
    if row.revoked_at:
        raise HTTPException(403, "Enrollment is revoked")
    return row


def preferences(row):
    # Persisted malformed data must surface as an error, not silently reset choices.
    return CommunicationPreferences.model_validate(json.loads(row.preferences_json or "{}"))


def preference_response(row):
    return {"version": row.profile_version or 0, "preferences": preferences(row).model_dump()}


def audit(db, user_id, actor, action, detail):
    db.add(EnrollmentAudit(id=str(uuid.uuid4()), user_id=user_id, actor=actor,
                          action=action, detail_json=json.dumps(detail)))


def update_profile(db, row, expected_version, values, actor, action):
    before = {key: getattr(row, key) for key in values}
    result = db.execute(update(ParticipantAccess).where(
        ParticipantAccess.user_id == row.user_id,
        ParticipantAccess.profile_version == expected_version,
        ParticipantAccess.revoked_at.is_(None),
    ).execution_options(synchronize_session=False).values(**values, profile_version=expected_version + 1))
    if not result.rowcount:
        db.rollback()
        raise HTTPException(409, "Profile changed; reload before saving")
    audit(db, row.user_id, actor, action, {"before": before, "after": values, "version": expected_version + 1})
    db.commit()
    db.refresh(row)
    return preference_response(row)


def profile_response(db, row):
    contact = db.get(PatientContact, row.patient_id) if row.patient_id else None
    return {"user_id": row.user_id, "patient_id": row.patient_id, "role": row.role,
            "revoked": row.revoked_at is not None, "readiness": row.readiness,
            "readiness_note": row.readiness_note, "reassess_on": row.reassess_on,
            "preferred_user_id": contact.preferred_user_id if contact else None,
            "contact_version": contact.version if contact else 0, **preference_response(row)}


def set_contact(db, source, body, actor):
    target = active_enrollment(db, body.target_user_id)
    if not source.patient_id or source.patient_id != target.patient_id:
        raise HTTPException(422, "Preferred respondents must be verified for the same patient")
    if any(row.role == "caregiver" and not row.caregiver_consent for row in (source, target)):
        raise HTTPException(403, "Caregiver permission is required")
    if not body.agreement_confirmed or not body.note.strip():
        raise HTTPException(422, "Confirm the agreed contact change and document it")
    if target.readiness not in {"ready", "with_support"}:
        raise HTTPException(409, "Review the target respondent's readiness before handoff")
    contact = db.get(PatientContact, source.patient_id)
    previous = contact.preferred_user_id if contact else None
    if contact:
        result = db.execute(update(PatientContact).where(PatientContact.patient_id == source.patient_id,
            PatientContact.version == body.expected_contact_version).execution_options(synchronize_session=False).values(
                preferred_user_id=target.user_id, version=body.expected_contact_version + 1,
                updated_at=datetime.now(timezone.utc)))
        if not result.rowcount:
            db.rollback()
            raise HTTPException(409, "Preferred contact changed; reload before saving")
    else:
        if body.expected_contact_version != 0:
            raise HTTPException(409, "Preferred contact changed; reload before saving")
        db.add(PatientContact(patient_id=source.patient_id, preferred_user_id=target.user_id, version=1))
    detail = {"patient_id": source.patient_id, "previous_user_id": previous,
              "preferred_user_id": target.user_id, "note": body.note.strip(),
              "agreement_confirmed": True, "version": body.expected_contact_version + 1}
    audit(db, source.user_id, actor, "preferred_contact_changed", detail)
    if source.user_id != target.user_id:
        audit(db, target.user_id, actor, "preferred_contact_changed", detail)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Preferred contact changed; reload before saving") from exc
    db.expire_all()
    return detail


def preferred_respondent(db, source):
    if source.role == "caregiver" and not source.caregiver_consent:
        raise HTTPException(403, "The source caregiver's patient permission is no longer valid")
    contact = db.get(PatientContact, source.patient_id) if source.patient_id else None
    if not contact:
        raise HTTPException(409, "No preferred respondent has been agreed")
    target = active_enrollment(db, contact.preferred_user_id)
    if target.patient_id != source.patient_id or (target.role == "caregiver" and not target.caregiver_consent):
        raise HTTPException(403, "Preferred respondent's patient permission is no longer valid")
    if target.readiness not in {"ready", "with_support"}:
        raise HTTPException(409, "Reassess the preferred respondent before sending a check-in")
    return target
