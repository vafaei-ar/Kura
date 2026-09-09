"""Kura push-service API.

Endpoints
---------
GET  /health                      liveness + config summary
POST /v1/devices/register         iOS app registers its push token for a user_id
POST /v1/checkins/start           provider triggers a check-in for a user_id
GET  /v1/devices/{user_id}        debug: inspect a registered device (no token leak)

Flow for /v1/checkins/start:
  1. authenticate the provider (X-Provider-Key)
  2. look up the patient's device by user_id
  3. ask VERA-cloud to create a session  -> session_id
  4. send a push carrying session_id to the phone
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone, timedelta
from typing import Dict, Set

import uuid

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Response,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import delete, select, update, or_
from sqlalchemy.orm import Session

from .console import CONSOLE_HTML

from . import auth as auth_lib
from . import notify_email
from . import settings_store
from . import participant_auth
from . import participant_profile
from .apns import APNsClient
from .config import Settings, get_settings
from .db import (
    Checkin as CheckinRow,
    Clinician as ClinicianRow,
    ClinicianNote as NoteRow,
    Device as DeviceRow,
    ParticipantAccess,
    EnrollmentAudit,
    build_engine,
    make_session_factory,
)
from .models import (
    AdminSettingsRequest,
    ChangePasswordRequest,
    CompleteCheckinRequest,
    DeviceRegistration,
    LoginRequest,
    NoteRequest,
    StartCheckinRequest,
    StartCheckinResponse,
    TriageActionRequest,
    OutcomeEvent,
    AskRequest,
    EnrollmentRequest, RedeemRequest,
    RecordingConsentRequest,
    PreferencesUpdate, ReadinessUpdate, ContactHandoff,
)
from .vera_client import VeraClient

SESSION_COOKIE = "kura_session"

logging.basicConfig(level=logging.INFO)

def _retry_alert_pass():
    # Each worker thread owns its own SQLAlchemy session, including shutdown.
    with _session_factory()() as db:
        rows = db.execute(select(CheckinRow).where(CheckinRow.alerted_at.is_(None), CheckinRow.has_priority.is_(True))).scalars().all()
        for row in rows:
            if row.summary_json:
                _maybe_send_emergency_alert(row, json.loads(row.summary_json), get_settings(), db)


async def retry_alerts():
    while True:
        try:
            await asyncio.to_thread(_retry_alert_pass)
            with _session_factory()() as db:
                for row in db.execute(select(CheckinRow).where(CheckinRow.needs_revoke.is_(True))).scalars().all():
                    try:
                        summary = await VeraClient(get_settings()).stop_session(row.session_id)
                        _store_summary(row, summary, db)
                        row.needs_revoke = False
                        row.session_token = None
                        db.commit()
                    except Exception:
                        logging.warning("Session revocation pending for %s", row.session_id)
        except Exception:
            logging.exception("Alert retry pass failed")
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    if settings.deployment_mode == "production":
        if len(settings.session_secret) < 32 or not settings.provider_api_key or not settings.vera_event_key:
            raise RuntimeError("Production requires strong SESSION_SECRET, PROVIDER_API_KEY and VERA_EVENT_KEY")
        if not settings.vera_api_base.startswith("https://") or not settings.vera_api_key:
            raise RuntimeError("Production requires HTTPS VERA_API_BASE and VERA_API_KEY")
    worker = asyncio.create_task(retry_alerts())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title="Kura push-service", version="0.2.0", lifespan=lifespan)

# Database engine + session factory (SQLite by default; Postgres via DATABASE_URL).
# Built lazily on first use so importing the module never touches disk.
_SessionLocal = None


def _session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        engine = build_engine(get_settings().database_url)
        _SessionLocal = make_session_factory(engine)
    return _SessionLocal


def get_db():
    db = _session_factory()()
    try:
        yield db
    finally:
        db.close()


def _device_dict(d: DeviceRow) -> dict:
    return {
        "user_id": d.user_id, "platform": d.platform, "token_type": d.token_type,
        "role": d.role, "display_name": d.display_name,
        "token_preview": d.push_token[:8] + "…", "app_version": d.app_version,
        "registered_at": d.registered_at, "updated_at": d.updated_at,
    }


def _checkin_dict(c: CheckinRow) -> dict:
    return {
        "session_id": c.session_id, "user_id": c.user_id, "scenario": c.scenario,
        "role": c.role, "started_at": c.started_at, "status": c.status,
        "has_priority": c.has_priority, "completed_at": c.completed_at,
        "acknowledged_at": c.acknowledged_at, "acknowledged_by": c.acknowledged_by,
        "resolved_at": c.resolved_at, "resolved_by": c.resolved_by,
        "summary_version": c.summary_version, "alert_state": c.alert_state,
        "alert_attempts": c.alert_attempts, "alert_next_at": c.alert_next_at,
        "owner": c.owner,
    }


class NotifyManager:
    """Tracks live app WebSocket connections per user_id.

    This is the FREE-TEAM push workaround: while the app is running it holds a
    socket here, and a triggered check-in is delivered down it instantly. It
    does NOT wake a closed app — that needs real APNs (paid program). The app
    swaps this transport for APNs by flipping Config.pushEnabled later.
    """

    def __init__(self) -> None:
        self._conns: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._conns.setdefault(user_id, set()).add(ws)

    async def disconnect(self, user_id: str, ws: WebSocket) -> None:
        async with self._lock:
            conns = self._conns.get(user_id)
            if conns:
                conns.discard(ws)
                if not conns:
                    self._conns.pop(user_id, None)

    async def deliver(self, user_id: str, payload: dict) -> int:
        """Send payload to all live sockets for user_id. Returns # delivered."""
        async with self._lock:
            targets = list(self._conns.get(user_id, set()))
        delivered = 0
        for ws in targets:
            try:
                await ws.send_json(payload)
                delivered += 1
            except Exception:
                await self.disconnect(user_id, ws)
        return delivered


notify_manager = NotifyManager()

# Pending check-in invites per user_id, for the POLLING delivery path (works on
# hosts without WebSockets, e.g. Azure free tier). start_checkin queues here; the
# app polls /v1/checkins/pending/{user_id}, which returns and clears the invite.
_pending: Dict[str, dict] = {}


def current_clinician(
    kura_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> ClinicianRow | None:
    """Resolve the logged-in clinician from the signed session cookie, or None."""
    if not kura_session:
        return None
    cid = auth_lib.verify_token(kura_session, settings.signing_secret)
    if not cid:
        return None
    row = db.get(ClinicianRow, cid)
    if row is None or not row.is_active:
        return None
    return row


def require_provider(
    x_provider_key: str | None = Header(default=None),
    clinician: ClinicianRow | None = Depends(current_clinician),
    settings: Settings = Depends(get_settings),
) -> ClinicianRow | None:
    """Gate for provider-facing endpoints.

    Accepts EITHER a valid clinician session cookie (preferred) OR the legacy
    shared X-Provider-Key (kept during the transition to per-clinician login).
    Returns the acting clinician (or None when the legacy key/no-auth path is
    used) so callers can attribute actions.
    """
    if clinician is not None:
        return clinician
    expected = settings.provider_api_key
    if not expected and settings.deployment_mode == "development":
        return None  # auth disabled (dev only)
    if expected and x_provider_key and secrets.compare_digest(x_provider_key, expected):
        return None  # authenticated via legacy shared key, no clinician identity
    raise HTTPException(status_code=401, detail="login required")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/console", response_class=HTMLResponse, include_in_schema=False)
def provider_console() -> str:
    """Provider web console (static single-page UI)."""
    return CONSOLE_HTML


@app.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "dry_run": settings.dry_run,
        "vera_configured": bool(settings.vera_api_base),
        "apns_sandbox": settings.apns_use_sandbox,
    }


# --- Clinician auth ------------------------------------------------------

def _clinician_dict(c: ClinicianRow) -> dict:
    return {
        "id": c.id, "username": c.username, "display_name": c.display_name,
        "role": c.role, "must_change_password": c.must_change_password,
    }


@app.post("/v1/auth/login")
def login(
    req: LoginRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> dict:
    """Clinician login. Verifies the password and sets a signed session cookie."""
    username = (req.username or "").strip().lower()
    row = db.execute(
        select(ClinicianRow).where(ClinicianRow.username == username)
    ).scalar_one_or_none()
    # Always run a verify to keep timing similar whether or not the user exists.
    stored = row.password_hash if row else "pbkdf2_sha256$200000$00$00"
    if not auth_lib.verify_password(req.password, stored) or row is None or not row.is_active:
        raise HTTPException(status_code=401, detail="invalid username or password")
    row.last_login_at = datetime.now(timezone.utc)
    db.commit()
    token = auth_lib.issue_token(row.id, settings.signing_secret, ttl_hours=settings.session_ttl_hours)
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax",
        secure=not settings.dry_run, max_age=settings.session_ttl_hours * 3600, path="/",
    )
    return {"clinician": _clinician_dict(row)}


@app.post("/v1/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/v1/auth/me")
def auth_me(clinician: ClinicianRow | None = Depends(current_clinician)) -> dict:
    if clinician is None:
        raise HTTPException(status_code=401, detail="not logged in")
    return {"clinician": _clinician_dict(clinician)}


@app.post("/v1/auth/change-password")
def change_password(
    req: ChangePasswordRequest,
    clinician: ClinicianRow | None = Depends(current_clinician),
    db: Session = Depends(get_db),
) -> dict:
    if clinician is None:
        raise HTTPException(status_code=401, detail="not logged in")
    if not auth_lib.verify_password(req.current_password, clinician.password_hash):
        raise HTTPException(status_code=400, detail="current password is incorrect")
    if len(req.new_password or "") < 8:
        raise HTTPException(status_code=400, detail="new password must be at least 8 characters")
    clinician.password_hash = auth_lib.hash_password(req.new_password)
    clinician.must_change_password = False
    db.commit()
    return {"ok": True, "clinician": _clinician_dict(clinician)}


# --- Admin settings (admin role only) ------------------------------------

def require_admin(
    clinician: ClinicianRow | None = Depends(current_clinician),
) -> ClinicianRow:
    """Gate admin-only endpoints. Requires a logged-in clinician with role=admin
    (the legacy shared-key path has no identity, so it can't reach admin)."""
    if clinician is None or clinician.role != "admin":
        raise HTTPException(status_code=403, detail="admin access required")
    return clinician


@app.get("/v1/admin/settings")
def get_admin_settings(
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
    _: ClinicianRow = Depends(require_admin),
) -> dict:
    """Current effective alert settings for the admin form. No secrets returned —
    only whether the SMTP password is set in the environment."""
    return settings_store.admin_view(db, settings)


@app.put("/v1/admin/settings")
def update_admin_settings(
    req: AdminSettingsRequest,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
    admin: ClinicianRow = Depends(require_admin),
) -> dict:
    """Update non-secret settings (only provided fields). Persists DB overrides
    that overlay the env defaults at runtime."""
    data = {k: v for k, v in req.model_dump(exclude_none=True).items()}
    settings_store.set_overrides(db, data, updated_by=admin.username)
    return settings_store.admin_view(db, settings)


@app.post("/v1/admin/test-email")
def admin_test_email(
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
    _: ClinicianRow = Depends(require_admin),
) -> dict:
    """Send a test alert email using the effective settings, so the admin can
    verify delivery without waiting for a real Tier-1 flag."""
    eff = settings_store.effective_settings(db, settings)
    ok, detail = notify_email.send_test(eff)
    return {"ok": ok, "detail": detail}


@app.post("/v1/devices/register")
def register_device(reg: DeviceRegistration, db: Session = Depends(get_db),
                    authorization: str | None = Header(default=None), settings: Settings = Depends(get_settings)) -> dict:
    access = participant_auth.require_user(reg.user_id, authorization, db, settings)
    now = datetime.now(timezone.utc)
    row = db.get(DeviceRow, reg.user_id)
    if row is None:
        row = DeviceRow(user_id=reg.user_id, registered_at=now)
        db.add(row)
    row.push_token = reg.push_token
    row.platform = reg.platform
    row.token_type = reg.token_type
    row.role = access.role if access else reg.role
    if reg.display_name:
        row.display_name = reg.display_name
    row.app_version = reg.app_version
    row.updated_at = now
    db.commit()
    return _device_dict(row)


@app.get("/v1/devices")
def list_devices(
    db: Session = Depends(get_db),
    _: None = Depends(require_provider),
) -> list[dict]:
    """List registered devices (tokens masked). Used by the provider console."""
    rows = db.execute(select(DeviceRow).order_by(DeviceRow.registered_at.desc())).scalars().all()
    return [_device_dict(d) for d in rows]


@app.get("/v1/devices/{user_id}")
def get_device(user_id: str, db: Session = Depends(get_db), _: None = Depends(require_provider)) -> dict:
    row = db.get(DeviceRow, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no device registered for user_id")
    return _device_dict(row)


@app.post("/v1/enrollments")
def create_enrollment(body: EnrollmentRequest, db: Session = Depends(get_db),
                      clinician: ClinicianRow | None = Depends(require_provider)):
    if db.get(ParticipantAccess, body.user_id):
        raise HTTPException(409, "Respondent already enrolled; revoke/reissue credentials explicitly")
    if body.role == "caregiver" and body.patient_id and not body.caregiver_consent:
        raise HTTPException(422, "Document authorized caregiver permission before linking a patient")
    code = secrets.token_urlsafe(24)
    row = ParticipantAccess(user_id=body.user_id, patient_id=body.patient_id,
        role=body.role, caregiver_consent=body.caregiver_consent,
        stroke_type=body.stroke_type, readiness_note=body.readiness_note,
        code_hash=participant_auth.digest(code), code_expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
        created_by=_actor_label(clinician))
    db.add(row)
    db.commit()
    return {"user_id": row.user_id, "enrollment_code": code, "expires_in_hours": 48}


@app.post("/v1/enrollments/redeem")
def redeem_enrollment(body: RedeemRequest, db: Session = Depends(get_db)):
    return participant_auth.redeem(body.code, db)


@app.get("/v1/participants/me/preferences")
def my_preferences(db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                   authorization: str | None = Header(default=None)):
    row = participant_auth.credential(authorization, db, settings)
    if row is None:
        raise HTTPException(401, "Verified participant credentials required")
    return participant_profile.preference_response(row)


@app.put("/v1/participants/me/preferences")
def save_my_preferences(body: PreferencesUpdate, db: Session = Depends(get_db),
                        settings: Settings = Depends(get_settings), authorization: str | None = Header(default=None)):
    row = participant_auth.credential(authorization, db, settings)
    if row is None:
        raise HTTPException(401, "Verified participant credentials required")
    return participant_profile.update_profile(db, row, body.expected_version,
        {"preferences_json": body.preferences.model_dump_json()}, "respondent:" + row.user_id, "preferences_changed")


@app.get("/v1/enrollments")
def list_enrollments(db: Session = Depends(get_db), _: None = Depends(require_provider)):
    return [participant_profile.profile_response(db, row)
            for row in db.execute(select(ParticipantAccess).order_by(ParticipantAccess.user_id)).scalars()]


@app.get("/v1/enrollments/{user_id}/profile")
def enrollment_profile(user_id: str, db: Session = Depends(get_db), _: None = Depends(require_provider)):
    row = db.get(ParticipantAccess, user_id)
    if row is None:
        raise HTTPException(404, "Unknown enrollment")
    result = participant_profile.profile_response(db, row)
    result["linked_respondents"] = [participant_profile.profile_response(db, other) for other in
        db.execute(select(ParticipantAccess).where(ParticipantAccess.patient_id == row.patient_id,
                                                  ParticipantAccess.revoked_at.is_(None))).scalars()] if row.patient_id else []
    result["audit"] = [{"actor": event.actor, "action": event.action, "at": event.created_at,
                        "detail": json.loads(event.detail_json)} for event in db.execute(
        select(EnrollmentAudit).where(EnrollmentAudit.user_id == user_id).order_by(EnrollmentAudit.created_at.desc()).limit(50)).scalars()]
    return result


@app.put("/v1/enrollments/{user_id}/readiness")
def save_readiness(user_id: str, body: ReadinessUpdate, db: Session = Depends(get_db),
                   clinician: ClinicianRow | None = Depends(require_provider)):
    row = participant_profile.active_enrollment(db, user_id)
    if body.readiness in {"ready", "with_support"} and not body.note.strip():
        raise HTTPException(422, "Document the agreed independent-use or support arrangement")
    participant_profile.update_profile(db, row, body.expected_version,
        {"readiness": body.readiness, "reassess_on": body.reassess_on.isoformat() if body.reassess_on else None,
         "readiness_note": body.note.strip()}, _actor_label(clinician), "readiness_changed")
    return participant_profile.profile_response(db, row)


@app.post("/v1/enrollments/{user_id}/handoff")
def handoff_contact(user_id: str, body: ContactHandoff, db: Session = Depends(get_db),
                    clinician: ClinicianRow | None = Depends(require_provider)):
    row = participant_profile.active_enrollment(db, user_id)
    return participant_profile.set_contact(db, row, body, _actor_label(clinician))


@app.post("/v1/enrollments/{user_id}/revoke")
def revoke_enrollment(user_id: str, db: Session = Depends(get_db),
                      clinician: ClinicianRow | None = Depends(require_provider)):
    access = db.get(ParticipantAccess, user_id)
    if access is None:
        raise HTTPException(404, "Unknown enrollment")
    access.revoked_at = datetime.now(timezone.utc)
    access.token_hash = None
    access.code_hash = None
    rows = db.execute(select(CheckinRow).where(CheckinRow.user_id == user_id)).scalars().all()
    for row in rows:
        row.needs_revoke = True
        row.session_token = None
        _add_note(db, row.session_id, _actor_label(clinician), "Respondent access revoked; engine session revocation queued")
    db.commit()
    return {"revoked": True, "engine_revocations_pending": len(rows)}


@app.post("/v1/enrollments/{user_id}/reissue")
def reissue_enrollment(user_id: str, db: Session = Depends(get_db),
                      clinician: ClinicianRow | None = Depends(require_provider)):
    access = db.get(ParticipantAccess, user_id)
    if access is None:
        raise HTTPException(404, "Unknown enrollment")
    revoke_enrollment(user_id, db, clinician)
    code = secrets.token_urlsafe(24)
    access.code_hash = participant_auth.digest(code)
    access.code_expires_at = datetime.now(timezone.utc) + timedelta(hours=48)
    access.token_hash = None
    access.revoked_at = None
    db.commit()
    return {"user_id": user_id, "enrollment_code": code, "expires_in_hours": 48}


@app.delete("/v1/devices/{user_id}")
def delete_device(
    user_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_provider),
) -> dict:
    """Remove a patient (and their check-ins) from the dashboard."""
    row = db.get(DeviceRow, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no device registered for user_id")
    db.execute(delete(CheckinRow).where(CheckinRow.user_id == user_id))
    db.delete(row)
    _pending.pop(user_id, None)
    db.commit()
    return {"deleted": user_id}


@app.post("/v1/checkins/start", response_model=StartCheckinResponse)
async def start_checkin(
    req: StartCheckinRequest,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
    _: None = Depends(require_provider),
) -> StartCheckinResponse:
    if req.use_preferred_contact:
        source = participant_profile.active_enrollment(db, req.user_id)
        target = participant_profile.preferred_respondent(db, source)
        req = req.model_copy(update={"user_id": target.user_id})
    device = db.get(DeviceRow, req.user_id)
    if device is None:
        raise HTTPException(
            status_code=404,
            detail=f"no device registered for user_id={req.user_id!r}",
        )

    # Role is a property of the participant (declared at registration), not a
    # per-check-in choice. Use the device's role.
    role = device.role or req.role
    access = db.get(ParticipantAccess, req.user_id)
    if access and access.revoked_at:
        raise HTTPException(403, "Respondent access revoked")
    if settings.deployment_mode == "production" and not access:
        raise HTTPException(403, "Provider-authored enrollment required")
    if access:
        role = access.role
        if access.readiness == "paused":
            raise HTTPException(409, "Check-ins are paused for this respondent; reassess readiness first")
        if settings.deployment_mode == "production" and access.readiness not in {"ready", "with_support"}:
            raise HTTPException(409, "Review app-use readiness before starting a production check-in")

    vera = VeraClient(settings)
    try:
        session_id = await vera.start_session(
            user_id=req.user_id,
            scenario=req.scenario,
            patient_name=req.patient_name or device.display_name or "",
            honorific=req.honorific,
            role=role,
            empathy=req.empathy,
            caregiver_consent=access.caregiver_consent if access else req.caregiver_consent,
            patient_id=access.patient_id if access else req.patient_id,
            stroke_type=access.stroke_type if access else req.stroke_type,
            rate=req.rate if req.rate is not None else participant_profile.preferences(access).speech_rate if access else None,
            communication_preferences=participant_profile.preferences(access).model_dump() if access else None,
        )
    except Exception as exc:  # surface VERA failures clearly to the provider
        raise HTTPException(status_code=502, detail=f"VERA session start failed: {exc}")

    # Persist before external delivery; polling survives process restarts and
    # the outcome receiver can correlate a session before the phone opens it.
    db.add(CheckinRow(session_id=session_id, user_id=req.user_id,
                     scenario=req.scenario, role=role, status="started", session_token=vera.session_token))
    db.commit()
    apns = APNsClient(settings)
    result = await apns.send_checkin(
        push_token=device.push_token,
        session_id=session_id,
        scenario=req.scenario,
    )

    invite = {"type": "checkin_invite", "session_id": session_id, "scenario": req.scenario, "session_token": vera.session_token}
    # Queue for the polling path (free-tier friendly)...
    _pending[req.user_id] = invite
    # ...and also push to any live WebSocket (instant path, when available).
    live_delivered = await notify_manager.deliver(req.user_id, invite)

    # Persist the check-in so the console can filter/report later.

    return StartCheckinResponse(
        session_id=session_id,
        user_id=req.user_id,
        push_sent=result.sent,
        push_dry_run=result.dry_run,
        live_delivered=live_delivered,
        detail=f"{result.detail}; live_delivered={live_delivered}",
    )


@app.get("/v1/checkins")
def list_checkins(
    db: Session = Depends(get_db),
    priority_only: bool = False,
    unresolved_priority: bool = False,
    failed_delivery: bool = False,
    unassigned: bool = False,
    user_id: str | None = None,
    _: ClinicianRow | None = Depends(require_provider),
) -> list[dict]:
    """Recent check-ins (most recent first), with optional filters for the
    red-flag report: priority_only shows only flagged check-ins;
    unresolved_priority shows flagged check-ins not yet resolved (the worklist)."""
    stmt = select(CheckinRow).order_by(CheckinRow.started_at.desc()).limit(200)
    if priority_only or unresolved_priority:
        stmt = stmt.where(CheckinRow.has_priority.is_(True))
    if unresolved_priority:
        stmt = stmt.where(CheckinRow.resolved_at.is_(None))
    if failed_delivery:
        stmt = stmt.where(CheckinRow.alert_state == "failed")
    if unassigned:
        stmt = stmt.where(CheckinRow.has_priority.is_(True), CheckinRow.owner.is_(None), CheckinRow.resolved_at.is_(None))
    if user_id:
        stmt = stmt.where(CheckinRow.user_id == user_id)
    return [_checkin_dict(c) for c in db.execute(stmt).scalars().all()]


@app.get("/v1/checkins/priority-count")
def priority_count(
    db: Session = Depends(get_db),
    _: ClinicianRow | None = Depends(require_provider),
) -> dict:
    """Counts for the dashboard badge: open (unresolved) priority items + total."""
    total_priority = db.execute(
        select(CheckinRow).where(CheckinRow.has_priority.is_(True))
    ).scalars().all()
    open_priority = [c for c in total_priority if c.resolved_at is None]
    return {"open_priority": len(open_priority), "total_priority": len(total_priority)}


@app.get("/v1/stats")
def dashboard_stats(
    db: Session = Depends(get_db),
    _: ClinicianRow | None = Depends(require_provider),
) -> dict:
    """Top-of-console summary: patients, open priority, awaiting first check-in,
    check-ins today, and median time-to-acknowledge for priority items (minutes)."""
    devices = db.execute(select(DeviceRow)).scalars().all()
    checkins = db.execute(select(CheckinRow)).scalars().all()

    users_with_checkin = {c.user_id for c in checkins}
    awaiting_first = sum(1 for d in devices if d.user_id not in users_with_checkin)

    now = datetime.now(timezone.utc)
    today = now.date()

    def _aware(dt):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    checkins_today = sum(1 for c in checkins if c.started_at and _aware(c.started_at).date() == today)

    priority = [c for c in checkins if c.has_priority]
    open_priority = [c for c in priority if c.resolved_at is None]

    # median minutes from started -> acknowledged across acknowledged priority items
    deltas = sorted(
        (_aware(c.acknowledged_at) - _aware(c.started_at)).total_seconds() / 60.0
        for c in priority if c.acknowledged_at and c.started_at
    )
    median_ack = None
    if deltas:
        m = len(deltas) // 2
        median_ack = round(deltas[m] if len(deltas) % 2 else (deltas[m - 1] + deltas[m]) / 2, 1)

    return {
        "patients": len(devices),
        "open_priority": len(open_priority),
        "total_priority": len(priority),
        "awaiting_first_checkin": awaiting_first,
        "checkins_today": checkins_today,
        "median_ack_minutes": median_ack,
    }


@app.get("/v1/patients/{user_id}")
def patient_detail(
    user_id: str,
    db: Session = Depends(get_db),
    _: ClinicianRow | None = Depends(require_provider),
) -> dict:
    """Per-patient view: the device plus their check-in timeline and a small
    flag-trend summary (most recent first)."""
    device = db.get(DeviceRow, user_id)
    if device is None:
        raise HTTPException(status_code=404, detail="no device registered for user_id")
    rows = db.execute(
        select(CheckinRow).where(CheckinRow.user_id == user_id)
        .order_by(CheckinRow.started_at.desc()).limit(200)
    ).scalars().all()
    checkins = [_checkin_dict(c) for c in rows]
    priority = [c for c in checkins if c["has_priority"]]
    return {
        "patient": _device_dict(device),
        "checkins": checkins,
        "summary": {
            "total": len(checkins),
            "priority": len(priority),
            "open_priority": len([c for c in priority if not c["resolved_at"]]),
            "last_checkin_at": checkins[0]["started_at"] if checkins else None,
        },
    }


@app.delete("/v1/checkins/{session_id}")
def delete_checkin(
    session_id: str,
    db: Session = Depends(get_db),
    _: ClinicianRow | None = Depends(require_provider),
) -> dict:
    """Remove a single check-in record from the dashboard."""
    row = db.get(CheckinRow, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no check-in for session_id")
    db.delete(row)
    db.commit()
    return {"deleted": session_id}


def _actor_label(clinician: ClinicianRow | None) -> str:
    """Human label for who performed a triage action (for the audit trail)."""
    if clinician is None:
        return "provider (shared key)"
    return f"{clinician.display_name} ({clinician.role})"


def _note_dict(n: NoteRow) -> dict:
    return {"id": n.id, "session_id": n.session_id, "author": n.author,
            "text": n.text, "created_at": n.created_at}


def _add_note(db: Session, session_id: str, author: str, text: str) -> NoteRow:
    note = NoteRow(id=str(uuid.uuid4()), session_id=session_id,
                   author=author, text=text.strip(),
                   created_at=datetime.now(timezone.utc))
    db.add(note)
    return note


@app.post("/v1/checkins/{session_id}/acknowledge")
def acknowledge_checkin(
    session_id: str,
    body: TriageActionRequest | None = None,
    db: Session = Depends(get_db),
    clinician: ClinicianRow | None = Depends(require_provider),
) -> dict:
    """Mark a check-in as seen by a clinician (triage step 1). Optional note."""
    row = db.get(CheckinRow, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no check-in for session_id")
    label = _actor_label(clinician)
    row.acknowledged_at = datetime.now(timezone.utc)
    row.acknowledged_by = label
    row.owner = body.owner if body and body.owner else label
    _add_note(db, session_id, label, f"Acknowledged; owner: {row.owner}")
    if body and (body.note or "").strip():
        _add_note(db, session_id, label, body.note)
    db.commit()
    return _checkin_dict(row)


@app.post("/v1/checkins/{session_id}/resolve")
def resolve_checkin(
    session_id: str,
    body: TriageActionRequest | None = None,
    db: Session = Depends(get_db),
    clinician: ClinicianRow | None = Depends(require_provider),
) -> dict:
    """Mark a check-in as resolved/followed-up (triage step 2). Acknowledges it
    too if that hadn't happened yet, so resolve always implies seen. Optional note."""
    row = db.get(CheckinRow, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no check-in for session_id")
    now = datetime.now(timezone.utc)
    label = _actor_label(clinician)
    if row.acknowledged_at is None:
        row.acknowledged_at = now
        row.acknowledged_by = label
    row.resolved_at = now
    row.resolved_by = label
    _add_note(db, session_id, label, "Resolved / followed up")
    if body and (body.note or "").strip():
        _add_note(db, session_id, label, body.note)
    db.commit()
    return _checkin_dict(row)


@app.get("/v1/checkins/{session_id}/notes")
def list_notes(
    session_id: str,
    db: Session = Depends(get_db),
    _: ClinicianRow | None = Depends(require_provider),
) -> list[dict]:
    rows = db.execute(
        select(NoteRow).where(NoteRow.session_id == session_id)
        .order_by(NoteRow.created_at.asc())
    ).scalars().all()
    return [_note_dict(n) for n in rows]


@app.post("/v1/checkins/{session_id}/notes")
def add_note(
    session_id: str,
    body: NoteRequest,
    db: Session = Depends(get_db),
    clinician: ClinicianRow | None = Depends(require_provider),
) -> dict:
    if not (body.text or "").strip():
        raise HTTPException(status_code=400, detail="note text is required")
    if db.get(CheckinRow, session_id) is None:
        raise HTTPException(status_code=404, detail="no check-in for session_id")
    note = _add_note(db, session_id, _actor_label(clinician), body.text)
    db.commit()
    return _note_dict(note)


@app.post("/v1/checkins/{session_id}/reopen")
def reopen_checkin(
    session_id: str,
    db: Session = Depends(get_db),
    _: ClinicianRow | None = Depends(require_provider),
) -> dict:
    """Undo resolve/acknowledge (e.g. clicked by mistake)."""
    row = db.get(CheckinRow, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no check-in for session_id")
    row.acknowledged_at = None
    _add_note(db, session_id, "workflow", f"Reopened; previous acknowledgement: {row.acknowledged_by}; resolution: {row.resolved_by}")
    row.acknowledged_by = None
    row.resolved_at = None
    row.resolved_by = None
    db.commit()
    return _checkin_dict(row)


async def _fetch_and_store_summary(
    session_id: str, settings: Settings, db: Session
) -> dict | None:
    """Get VERA's clinician summary and persist it on the check-in row."""
    vera = VeraClient(settings)
    try:
        summary = await vera.clinician_summary(session_id)
    except Exception:
        logging.warning("Could not refresh summary for %s", session_id)
        return None
    if summary is None:
        return None
    row = db.get(CheckinRow, session_id)
    if row is not None:
        _store_summary(row, summary, db)
        _maybe_send_emergency_alert(row, summary, settings, db)
    return summary


def _summary_min_tier(summary: dict) -> int:
    """Lowest (most severe) tier across the summary's priority items. 3 if none."""
    tiers = [
        f.get("tier") for f in (summary.get("priority_items") or [])
        if isinstance(f, dict) and isinstance(f.get("tier"), int)
    ]
    return min(tiers) if tiers else 3


def _maybe_send_emergency_alert(row: CheckinRow, summary: dict,
                                settings: Settings, db: Session) -> None:
    """Retry email acceptance for a Tier-1 (emergency) concern.
    Tier-2/urgent stay in the console worklist to avoid alert fatigue."""
    if row.alerted_at is not None:
        return
    if _summary_min_tier(summary) != 1:
        return
    now = datetime.now(timezone.utc)
    # Atomic lease prevents two workers from sending simultaneously. SMTP is
    # at-least-once: a crash after acceptance can still lead to a duplicate.
    claimed = db.execute(update(CheckinRow).where(
        CheckinRow.session_id == row.session_id, CheckinRow.alerted_at.is_(None),
        or_(CheckinRow.alert_next_at.is_(None), CheckinRow.alert_next_at <= now),
    ).execution_options(synchronize_session=False).values(alert_state="sending", alert_next_at=now + timedelta(minutes=2),
             alert_attempts=CheckinRow.alert_attempts + 1))
    db.commit()
    if not claimed.rowcount:
        return
    db.refresh(row)
    eff = settings_store.effective_settings(db, settings)
    try:
        sent = notify_email.send_alert(eff, row.user_id, row.session_id, tier=1)
    except Exception:
        logging.exception("Alert delivery failed")
        sent = False
    row.alert_state = "smtp_accepted" if sent else "failed"
    if sent:
        row.alerted_at = now
        row.alert_next_at = None
    else:
        row.alert_next_at = now + timedelta(seconds=min(3600, 30 * 2 ** min(row.alert_attempts, 7)))
    db.commit()


def _store_summary(row, summary, db):
    version = int(summary.get("version", 0))
    if not version and (row.summary_version or 0) > 0:
        return  # a legacy/unversioned response cannot overwrite newer evidence
    if version and version <= (row.summary_version or 0):
        return
    previous = json.loads(row.summary_json or "{}")
    def concerns(value):
        return {json.dumps({key: flag.get(key) for key in ("rule_id", "tier", "matched")}, sort_keys=True)
                for flag in value.get("priority_items", []) if isinstance(flag, dict)}
    new_flags = concerns(summary) - concerns(previous)
    urgency_rank = {None: 0, "routine": 0, "soon": 1, "unsure": 1, "urgent": 2}
    new_concern = bool(new_flags or (summary.get("callback_requested") and not previous.get("callback_requested"))
        or urgency_rank.get(summary.get("user_reported_urgency"), 0) > urgency_rank.get(previous.get("user_reported_urgency"), 0))
    values = dict(summary_json=json.dumps(summary), has_priority=bool(summary.get("has_priority")), summary_version=version)
    reopen = new_concern and row.resolved_at is not None
    if reopen:
        values.update(resolved_at=None, resolved_by=None, acknowledged_at=None, acknowledged_by=None)
    if new_flags and _summary_min_tier(summary) == 1 and row.alerted_at is not None:
        values.update(alerted_at=None, alert_next_at=None, alert_state="pending")
    state = summary.get("state")
    if state in {"completed", "declined", "withdrawn", "interrupted", "escalated", "in_progress"}:
        values["status"] = state
        if state == "completed":
            values["completed_at"] = row.completed_at or datetime.now(timezone.utc)
    stmt = update(CheckinRow).where(CheckinRow.session_id == row.session_id,
                                   CheckinRow.summary_version == (row.summary_version or 0))
    changed = db.execute(stmt.execution_options(synchronize_session=False).values(**values))
    if not changed.rowcount:
        db.rollback()
        db.refresh(row)
        return _store_summary(row, summary, db)
    if reopen:
        _add_note(db, row.session_id, "workflow", f"New concern in outcome version {version}; reopened. Previous resolution: {row.resolved_by}; owner retained: {row.owner}")
    db.commit()
    db.refresh(row)


@app.post("/v1/vera/events")
async def ingest_outcome(event: OutcomeEvent, x_event_key: str = Header(default=""),
                         settings: Settings = Depends(get_settings), db: Session = Depends(get_db)):
    if not settings.vera_event_key or not secrets.compare_digest(x_event_key, settings.vera_event_key):
        raise HTTPException(401, "Invalid event credentials")
    row = db.get(CheckinRow, event.session_id)
    if row is None:
        raise HTTPException(409, "Session not registered yet; retry delivery")
    if event.summary.get("session_id") != event.session_id or event.summary.get("version") != event.version:
        raise HTTPException(422, "Event and summary identity/version must agree")
    _store_summary(row, event.summary, db)
    _maybe_send_emergency_alert(row, json.loads(row.summary_json), settings, db)
    return {"accepted_version": row.summary_version, "alert_state": row.alert_state}


@app.get("/v1/checkins/{session_id}/summary")
async def checkin_summary(
    session_id: str,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
    _: None = Depends(require_provider),
) -> dict:
    """Clinician summary (flags + tiers). Returns the stored copy if we have it,
    else fetches from VERA (and stores it). {"ready": false} until available."""
    row = db.get(CheckinRow, session_id)
    summary = await _fetch_and_store_summary(session_id, settings, db)
    if row is not None and row.summary_json:
        return {"ready": True, "summary": json.loads(row.summary_json), "stored": True}
    if summary is None:
        return {"ready": False, "session_id": session_id}
    return {"ready": True, "summary": summary}


@app.post("/v1/checkins/{session_id}/complete")
async def complete_checkin(
    session_id: str,
    body: CompleteCheckinRequest | None = None,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> dict:
    """Called by the app when the check-in ends. Records the patient's
    self-reported urgency (if any) in VERA, marks the check-in complete, and
    captures VERA's clinician summary (flags) into the database for reporting."""
    row = db.get(CheckinRow, session_id)
    if row is None:
        raise HTTPException(404, "Unknown check-in")
    participant_auth.require_user(row.user_id, authorization, db, settings)
    role = row.role if row is not None else "survivor"

    # Self-reported urgency first, so it's reflected in the summary we fetch.
    if body and body.urgency:
        vera = VeraClient(settings)
        try:
            await vera.record_urgency(session_id, body.urgency, role=role)
        except Exception as exc:
            raise HTTPException(502, "Urgency was not saved; please retry") from exc

    summary = await _fetch_and_store_summary(session_id, settings, db)
    if summary is None:
        raise HTTPException(503, "Outcome not available yet; retry")
    return {"ok": True, "state": row.status, "saved": True, "version": row.summary_version,
            "alert_state": row.alert_state, "has_priority": row.has_priority}


@app.post("/v1/checkins/{session_id}/decline")
async def decline_checkin(session_id: str, settings: Settings = Depends(get_settings), db: Session = Depends(get_db), authorization: str | None = Header(default=None)):
    row = db.get(CheckinRow, session_id)
    if row is None:
        raise HTTPException(404, "Unknown check-in")
    participant_auth.require_user(row.user_id, authorization, db, settings)
    try:
        summary = await VeraClient(settings).stop_session(session_id)
    except Exception as exc:
        raise HTTPException(502, "Could not confirm that the check-in was stopped") from exc
    _store_summary(row, summary, db)
    return {"ok": True, "state": row.status}


@app.get("/v1/resources")
async def resources(
    region: str | None = None,
    need: str | None = None,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Patient-facing: curated local resources (info-only) proxied from VERA.
    Open to the app (no provider key) — it carries no clinical content."""
    data = await VeraClient(settings).get_resources(region, need)
    return data or {"resources": {}, "disclaimer": "Resources are currently unavailable."}


@app.get("/v1/capabilities")
async def capabilities(settings: Settings = Depends(get_settings)):
    try:
        return await VeraClient(settings).capabilities()
    except Exception:
        return {"original_audio": False}


@app.post("/v1/checkins/{session_id}/recording-consent")
async def recording_consent(session_id: str, body: RecordingConsentRequest,
        db: Session = Depends(get_db), settings: Settings = Depends(get_settings), authorization: str | None = Header(default=None)):
    row = db.get(CheckinRow, session_id)
    if row is None:
        raise HTTPException(404, "Unknown check-in")
    access = participant_auth.require_user(row.user_id, authorization, db, settings)
    if access is None:
        raise HTTPException(403, "Verified enrollment required for original recording")
    try:
        return await VeraClient(settings).recording_consent(session_id, body.accepted)
    except Exception as exc:
        raise HTTPException(409, "Original audio consent could not be saved") from exc


@app.post("/v1/checkins/{session_id}/audio/{clip_id}")
async def upload_original_audio(session_id: str, clip_id: str, request: Request,
        db: Session = Depends(get_db), settings: Settings = Depends(get_settings), authorization: str | None = Header(default=None)):
    row = db.get(CheckinRow, session_id)
    if row is None:
        raise HTTPException(404, "Unknown check-in")
    access = participant_auth.require_user(row.user_id, authorization, db, settings)
    if access is None or row.needs_revoke:
        raise HTTPException(403, "Active verified enrollment required for original recording")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 10_000_000:
            raise HTTPException(413, "Recording too large")
    status, result = await VeraClient(settings).upload_audio(session_id, clip_id, bytes(content),
        request.headers.get("x-audio-partial") == "true")
    return Response(content=json.dumps(result), status_code=status, media_type="application/json")


@app.get("/v1/checkins/{session_id}/audio/{clip_id}")
async def original_audio(session_id: str, clip_id: str, db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings), clinician: ClinicianRow | None = Depends(require_provider)):
    if clinician is None and not settings.provider_api_key:
        raise HTTPException(403, "Authenticated clinician access required for original recording")
    if db.get(CheckinRow, session_id) is None:
        raise HTTPException(404, "Unknown check-in")
    content = await VeraClient(settings).original_audio(session_id, clip_id, _actor_label(clinician))
    if content is None:
        raise HTTPException(404, "Original recording unavailable")
    return Response(content=content, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.get("/v1/resource-regions")
async def resource_regions(settings: Settings = Depends(get_settings)) -> dict:
    data = await VeraClient(settings).get_resource_regions()
    return data or {"regions": [], "needs": ["transportation", "meals", "rehab", "devices", "support"]}


@app.post("/v1/ask")
async def ask(body: AskRequest, settings: Settings = Depends(get_settings), db: Session = Depends(get_db), authorization: str | None = Header(default=None)) -> dict:
    """Proxy to VERA's retrieval-only Ask-VERA. If VERA has it disabled (or no
    VERA), returns a graceful 'unavailable' message. The app also gates this
    behind Config.askVeraEnabled, so it ships off at multiple points."""
    question = body.question.strip()
    if body.user_id:
        participant_auth.require_user(body.user_id, authorization, db, settings)
    elif settings.deployment_mode == "production":
        raise HTTPException(401, "Participant sign-in required")
    if not question:
        return {"kind": "refusal", "answer": "Please type a question."}
    sid = None
    if body.share_with_team:
        if not body.user_id or not db.get(DeviceRow, body.user_id):
            raise HTTPException(404, "A registered respondent is needed to share a question")
        sid = "ask-" + (body.request_id or str(uuid.uuid4()))
        existing = db.get(CheckinRow, sid)
        if existing is not None and existing.user_id != body.user_id:
            raise HTTPException(409, "Request already belongs to another respondent")
        if existing is None:
            db.add(CheckinRow(session_id=sid, user_id=body.user_id, scenario="ask", status="in_progress"))
            db.commit()
    data = await VeraClient(settings).ask(question, session_id=sid,
        share_with_team=body.share_with_team, callback_requested=body.callback_requested)
    if data and data.get("saved") and sid:
        if data.get("summary"):
            _store_summary(db.get(CheckinRow, sid), data.pop("summary"), db)
        data["answer"] += " Your question was saved for the care team. Review and response are not yet confirmed."
        if body.callback_requested:
            data["answer"] += " Your request for human follow-up was also saved."
    elif data and body.share_with_team:
        data["answer"] += " Your question was not saved for the care team. Contact them directly if you need help."
    return data or {
        "kind": "refusal",
        "answer": "This isn't available right now. For any health concern, contact "
                  "your care team. If this is an emergency, call 911.",
    }


@app.get("/v1/checkins/pending/{user_id}")
def poll_pending(user_id: str, include_received: bool = False, db: Session = Depends(get_db), authorization: str | None = Header(default=None), settings: Settings = Depends(get_settings)) -> dict:
    """Durable invitations; explicit opt-in also discovers unfinished check-ins.

    Recovery is bounded by the one-hour VERA session credential, not indefinite.
    Reading never consumes an invitation.
    """
    participant_auth.require_user(user_id, authorization, db, settings)
    query = select(CheckinRow).where(CheckinRow.user_id == user_id, CheckinRow.needs_revoke.is_(False),
        CheckinRow.status.in_(["started", "awaiting_consent", "in_progress", "interrupted"]))
    if include_received:
        query = query.where(CheckinRow.started_at > datetime.now(timezone.utc) - timedelta(hours=1))
    else:
        query = query.where(CheckinRow.invite_received_at.is_(None))
    row = db.execute(query.order_by(CheckinRow.started_at)).scalars().first()
    return {"invite": {"type": "checkin_invite", "session_id": row.session_id, "scenario": row.scenario, "session_token": row.session_token} if row else None}


@app.post("/v1/checkins/{session_id}/received")
def acknowledge_invite(session_id: str, db: Session = Depends(get_db), authorization: str | None = Header(default=None), settings: Settings = Depends(get_settings)):
    row = db.get(CheckinRow, session_id)
    if row is None:
        raise HTTPException(404, "Unknown check-in")
    participant_auth.require_user(row.user_id, authorization, db, settings)
    row.invite_received_at = row.invite_received_at or datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@app.post("/v1/checkins/{session_id}/answer-receipt")
async def participant_answer_receipt(session_id: str, request: Request, db: Session = Depends(get_db),
        authorization: str | None = Header(default=None), settings: Settings = Depends(get_settings)):
    row = db.get(CheckinRow, session_id)
    if row is None:
        raise HTTPException(404, "Unknown check-in")
    participant_auth.require_user(row.user_id, authorization, db, settings)
    if row.needs_revoke:
        raise HTTPException(403, "Session access revoked")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 100_000:
            raise HTTPException(413, "Answer recovery payload too large")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Expected answer object")
        result = await VeraClient(settings).answer_receipt(session_id, payload)
    except Exception:
        raise HTTPException(503, "Could not verify this answer; local copy must be retained")
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@app.get("/v1/checkins/{session_id}/connection")
def session_connection(session_id: str, db: Session = Depends(get_db), authorization: str | None = Header(default=None), settings: Settings = Depends(get_settings)):
    row = db.get(CheckinRow, session_id)
    if row is None:
        raise HTTPException(404, "Unknown check-in")
    participant_auth.require_user(row.user_id, authorization, db, settings)
    if row.needs_revoke:
        raise HTTPException(403, "Session access revoked")
    return {"session_token": row.session_token}


@app.websocket("/v1/notify/{user_id}")
async def notify_ws(websocket: WebSocket, user_id: str) -> None:
    """The app holds this open to receive check-in invites in real time
    (free-team alternative to APNs). Sends a 'connected' ack, then streams
    invite payloads delivered via NotifyManager.
    """
    with _session_factory()() as db:
        try:
            participant_auth.require_user(user_id, websocket.headers.get("authorization"), db, get_settings())
        except HTTPException:
            await websocket.close(code=1008)
            return
    await websocket.accept()
    await notify_manager.connect(user_id, websocket)
    await websocket.send_json({"type": "connected", "user_id": user_id})
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        await notify_manager.disconnect(user_id, websocket)


# Scripted mock conversation (text-only; the app speaks it with on-device TTS).
_MOCK_GREETING = "Hello, this is your VERA check-in. How are you feeling today?"
_MOCK_QUESTIONS = [
    "Thanks for sharing. Have you taken your medications today?",
    "Good. Any new weakness, numbness, or trouble speaking?",
    "Understood. Is there anything else you'd like the care team to know?",
]
_MOCK_CLOSING = "Thank you. Your care team will review your check-in. Take care."


@app.websocket("/ws/audio/{session_id}")
async def mock_audio_ws(websocket: WebSocket, session_id: str) -> None:
    """DEV MOCK of VERA-cloud's audio socket — implements VERA's JSON protocol.

    Drives a scripted check-in so the iOS voice loop (on-device TTS + speech
    recognition) is testable WITHOUT Azure/VERA: greet, then for each
    `text_input` reply with the next question, then a `completion`. In
    production the app points Config.veraBaseURL at real VERA-cloud and this
    endpoint is unused.
    """
    await websocket.accept()
    total = len(_MOCK_QUESTIONS) + 1
    await websocket.send_json({"type": "greeting", "text": _MOCK_GREETING, "progress": 0})
    idx = 0
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if not text:
                continue  # ignore binary archival audio in the mock
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if data.get("type") != "text_input":
                continue
            spoken = (data.get("text") or "").lower()
            logging.info("mock /ws/audio %s heard: %r", session_id, data.get("text"))
            # Demo of VERA's red-flag path: trigger words raise an emergency alert.
            if any(w in spoken for w in (
                "face", "arm", "speech", "slurred", "weak", "numb", "911", "can't move"
            )):
                await websocket.send_json({
                    "type": "emergency_alert",
                    "message": "What you described may be a sign of a stroke. "
                               "If you are having these symptoms now, call 911 right away.",
                })
            if idx < len(_MOCK_QUESTIONS):
                await websocket.send_json({
                    "type": "response",
                    "text": _MOCK_QUESTIONS[idx],
                    "progress": int((idx + 1) / total * 100),
                })
                idx += 1
            else:
                await websocket.send_json({
                    "type": "completion",
                    "text": _MOCK_CLOSING,
                    "progress": 100,
                })
    except WebSocketDisconnect:
        pass
