"""Participant credentials and server-authored patient/response relationships."""
import hashlib
import secrets
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException
from sqlalchemy import select, update

from .db import ParticipantAccess


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def credential(authorization, db, settings):
    if not authorization:
        if settings.deployment_mode == "development":
            return None
        raise HTTPException(401, "Participant sign-in required")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid participant credentials")
    token = authorization[7:]
    now = datetime.now(timezone.utc)
    row = db.execute(select(ParticipantAccess).where(
        ParticipantAccess.token_hash == digest(token),
        ParticipantAccess.revoked_at.is_(None),
        ParticipantAccess.token_expires_at > now,
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(401, "Participant sign-in expired or revoked")
    return row


def require_user(user_id, authorization, db, settings):
    access = credential(authorization, db, settings)
    # An enrolled ID cannot fall back to the insecure demo route, even in dev.
    if access is None and db.get(ParticipantAccess, user_id) is not None:
        raise HTTPException(401, "Credentials required for this enrolled respondent")
    if access is not None and access.user_id != user_id:
        raise HTTPException(403, "This record belongs to another respondent")
    return access


def redeem(code, db):
    now = datetime.now(timezone.utc)
    hashed = digest(code.strip())
    row = db.execute(select(ParticipantAccess).where(
        ParticipantAccess.code_hash == hashed, ParticipantAccess.code_expires_at > now,
        ParticipantAccess.revoked_at.is_(None),
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(401, "Enrollment code invalid, expired, or already used")
    token = secrets.token_urlsafe(32)
    changed = db.execute(update(ParticipantAccess).where(
        ParticipantAccess.user_id == row.user_id, ParticipantAccess.code_hash == hashed,
    ).values(code_hash=None, token_hash=digest(token), token_expires_at=now + timedelta(days=30)))
    db.commit()
    if not changed.rowcount:
        raise HTTPException(409, "Enrollment code already redeemed")
    return {"token": token, "user_id": row.user_id, "role": row.role, "expires_in_days": 30}
