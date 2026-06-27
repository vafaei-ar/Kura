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
from datetime import datetime, timezone
from typing import Dict, Set

import uuid

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .console import CONSOLE_HTML

from . import auth as auth_lib
from . import notify_email
from .apns import APNsClient
from .config import Settings, get_settings
from .db import (
    Checkin as CheckinRow,
    Clinician as ClinicianRow,
    ClinicianNote as NoteRow,
    Device as DeviceRow,
    build_engine,
    make_session_factory,
)
from .models import (
    ChangePasswordRequest,
    CompleteCheckinRequest,
    DeviceRegistration,
    LoginRequest,
    NoteRequest,
    StartCheckinRequest,
    StartCheckinResponse,
    TriageActionRequest,
)
from .vera_client import VeraClient

SESSION_COOKIE = "kura_session"

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Kura push-service", version="0.1.0")

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
    if not expected:
        return None  # auth disabled (dev only)
    if x_provider_key == expected:
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


@app.post("/v1/devices/register")
def register_device(reg: DeviceRegistration, db: Session = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc)
    row = db.get(DeviceRow, reg.user_id)
    if row is None:
        row = DeviceRow(user_id=reg.user_id, registered_at=now)
        db.add(row)
    row.push_token = reg.push_token
    row.platform = reg.platform
    row.token_type = reg.token_type
    row.role = reg.role
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
def get_device(user_id: str, db: Session = Depends(get_db)) -> dict:
    row = db.get(DeviceRow, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no device registered for user_id")
    return _device_dict(row)


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
    device = db.get(DeviceRow, req.user_id)
    if device is None:
        raise HTTPException(
            status_code=404,
            detail=f"no device registered for user_id={req.user_id!r}",
        )

    # Role is a property of the participant (declared at registration), not a
    # per-check-in choice. Use the device's role.
    role = device.role or req.role

    vera = VeraClient(settings)
    try:
        session_id = await vera.start_session(
            user_id=req.user_id,
            scenario=req.scenario,
            patient_name=req.patient_name or device.display_name or "",
            honorific=req.honorific,
            role=role,
            empathy=req.empathy,
        )
    except Exception as exc:  # surface VERA failures clearly to the provider
        raise HTTPException(status_code=502, detail=f"VERA session start failed: {exc}")

    apns = APNsClient(settings)
    result = await apns.send_checkin(
        push_token=device.push_token,
        session_id=session_id,
        scenario=req.scenario,
    )

    invite = {"type": "checkin_invite", "session_id": session_id, "scenario": req.scenario}
    # Queue for the polling path (free-tier friendly)...
    _pending[req.user_id] = invite
    # ...and also push to any live WebSocket (instant path, when available).
    live_delivered = await notify_manager.deliver(req.user_id, invite)

    # Persist the check-in so the console can filter/report later.
    db.add(CheckinRow(
        session_id=session_id, user_id=req.user_id,
        scenario=req.scenario, role=role,
        started_at=datetime.now(timezone.utc), status="started",
    ))
    db.commit()

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
    summary = await vera.clinician_summary(session_id)
    if summary is None:
        return None
    row = db.get(CheckinRow, session_id)
    if row is not None:
        row.summary_json = json.dumps(summary)
        row.has_priority = bool(summary.get("has_priority"))
        db.commit()
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
    """Email the on-call clinician on a Tier-1 (emergency) flag, exactly once.
    Tier-2/urgent stay in the console worklist to avoid alert fatigue."""
    if row.alerted_at is not None:
        return
    if _summary_min_tier(summary) != 1:
        return
    notify_email.send_alert(settings, row.user_id, row.session_id, tier=1)
    # Mark as alerted regardless of send success, so a misconfigured SMTP doesn't
    # retry on every poll; logs capture failures.
    row.alerted_at = datetime.now(timezone.utc)
    db.commit()


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
    if row is not None and row.summary_json:
        return {"ready": True, "summary": json.loads(row.summary_json), "stored": True}
    summary = await _fetch_and_store_summary(session_id, settings, db)
    if summary is None:
        return {"ready": False, "session_id": session_id}
    return {"ready": True, "summary": summary}


@app.post("/v1/checkins/{session_id}/complete")
async def complete_checkin(
    session_id: str,
    body: CompleteCheckinRequest | None = None,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> dict:
    """Called by the app when the check-in ends. Records the patient's
    self-reported urgency (if any) in VERA, marks the check-in complete, and
    captures VERA's clinician summary (flags) into the database for reporting."""
    row = db.get(CheckinRow, session_id)
    role = row.role if row is not None else "survivor"

    # Self-reported urgency first, so it's reflected in the summary we fetch.
    if body and body.urgency:
        vera = VeraClient(settings)
        try:
            await vera.record_urgency(session_id, body.urgency, role=role)
        except Exception as exc:
            logging.warning("urgency record failed for %s: %s", session_id, exc)

    if row is not None:
        row.status = "completed"
        row.completed_at = datetime.now(timezone.utc)
        db.commit()
    summary = await _fetch_and_store_summary(session_id, settings, db)
    return {"ok": True, "has_priority": bool(summary.get("has_priority")) if summary else None}


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


@app.get("/v1/resource-regions")
async def resource_regions(settings: Settings = Depends(get_settings)) -> dict:
    data = await VeraClient(settings).get_resource_regions()
    return data or {"regions": [], "needs": ["transportation", "meals", "rehab", "devices", "support"]}


@app.post("/v1/ask")
async def ask(body: dict, settings: Settings = Depends(get_settings)) -> dict:
    """Proxy to VERA's retrieval-only Ask-VERA. If VERA has it disabled (or no
    VERA), returns a graceful 'unavailable' message. The app also gates this
    behind Config.askVeraEnabled, so it ships off at multiple points."""
    question = str(body.get("question", "")).strip()
    if not question:
        return {"kind": "refusal", "answer": "Please type a question."}
    data = await VeraClient(settings).ask(question)
    return data or {
        "kind": "refusal",
        "answer": "This isn't available right now. For any health concern, contact "
                  "your care team. If this is an emergency, call 911.",
    }


@app.get("/v1/checkins/pending/{user_id}")
def poll_pending(user_id: str) -> dict:
    """The app polls this every few seconds. Returns the queued check-in invite
    (and clears it) or null. Works on hosts without WebSockets (Azure free tier).
    """
    return {"invite": _pending.pop(user_id, None)}


@app.websocket("/v1/notify/{user_id}")
async def notify_ws(websocket: WebSocket, user_id: str) -> None:
    """The app holds this open to receive check-in invites in real time
    (free-team alternative to APNs). Sends a 'connected' ack, then streams
    invite payloads delivered via NotifyManager.
    """
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
