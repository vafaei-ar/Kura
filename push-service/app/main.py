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

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .console import CONSOLE_HTML

from .apns import APNsClient
from .config import Settings, get_settings
from .db import Checkin as CheckinRow, Device as DeviceRow, build_engine, make_session_factory
from .models import (
    CompleteCheckinRequest,
    DeviceRegistration,
    StartCheckinRequest,
    StartCheckinResponse,
)
from .vera_client import VeraClient

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
        "role": d.role, "token_preview": d.push_token[:8] + "…", "app_version": d.app_version,
        "registered_at": d.registered_at, "updated_at": d.updated_at,
    }


def _checkin_dict(c: CheckinRow) -> dict:
    return {
        "session_id": c.session_id, "user_id": c.user_id, "scenario": c.scenario,
        "role": c.role, "started_at": c.started_at, "status": c.status,
        "has_priority": c.has_priority, "completed_at": c.completed_at,
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


def require_provider(
    x_provider_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Simple shared-secret gate for provider-facing endpoints."""
    expected = settings.provider_api_key
    if not expected:
        return  # auth disabled (dev only)
    if x_provider_key != expected:
        raise HTTPException(status_code=401, detail="invalid or missing X-Provider-Key")


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
            patient_name=req.patient_name,
            honorific=req.honorific,
            role=role,
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
    user_id: str | None = None,
    _: None = Depends(require_provider),
) -> list[dict]:
    """Recent check-ins (most recent first), with optional filters for the
    red-flag report: priority_only=true shows only flagged check-ins."""
    stmt = select(CheckinRow).order_by(CheckinRow.started_at.desc()).limit(200)
    if priority_only:
        stmt = stmt.where(CheckinRow.has_priority.is_(True))
    if user_id:
        stmt = stmt.where(CheckinRow.user_id == user_id)
    return [_checkin_dict(c) for c in db.execute(stmt).scalars().all()]


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
    return summary


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
