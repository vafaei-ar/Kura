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
from typing import Dict, Set

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)

from .apns import APNsClient
from .config import Settings, get_settings
from .models import (
    Device,
    DeviceRegistration,
    StartCheckinRequest,
    StartCheckinResponse,
)
from .store import DeviceStore
from .vera_client import VeraClient

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Kura push-service", version="0.1.0")

# Single process-wide store; created from settings at import time.
_store = DeviceStore(get_settings().device_store_path)


def get_store() -> DeviceStore:
    return _store


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


@app.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "dry_run": settings.dry_run,
        "vera_configured": bool(settings.vera_api_base),
        "apns_sandbox": settings.apns_use_sandbox,
    }


@app.post("/v1/devices/register", response_model=Device)
def register_device(
    reg: DeviceRegistration,
    store: DeviceStore = Depends(get_store),
) -> Device:
    return store.upsert(reg)


@app.get("/v1/devices/{user_id}")
def get_device(user_id: str, store: DeviceStore = Depends(get_store)) -> dict:
    device = store.get(user_id)
    if device is None:
        raise HTTPException(status_code=404, detail="no device registered for user_id")
    # Never echo the full token back.
    return {
        "user_id": device.user_id,
        "platform": device.platform,
        "token_type": device.token_type,
        "token_preview": device.push_token[:8] + "…",
        "app_version": device.app_version,
        "registered_at": device.registered_at,
        "updated_at": device.updated_at,
    }


@app.post("/v1/checkins/start", response_model=StartCheckinResponse)
async def start_checkin(
    req: StartCheckinRequest,
    settings: Settings = Depends(get_settings),
    store: DeviceStore = Depends(get_store),
    _: None = Depends(require_provider),
) -> StartCheckinResponse:
    device = store.get(req.user_id)
    if device is None:
        raise HTTPException(
            status_code=404,
            detail=f"no device registered for user_id={req.user_id!r}",
        )

    vera = VeraClient(settings)
    try:
        session_id = await vera.start_session(
            user_id=req.user_id,
            scenario=req.scenario,
            patient_name=req.patient_name,
            honorific=req.honorific,
        )
    except Exception as exc:  # surface VERA failures clearly to the provider
        raise HTTPException(status_code=502, detail=f"VERA session start failed: {exc}")

    apns = APNsClient(settings)
    result = await apns.send_checkin(
        push_token=device.push_token,
        session_id=session_id,
        scenario=req.scenario,
    )

    # Free-team workaround: also deliver over any live app WebSocket.
    live_delivered = await notify_manager.deliver(
        req.user_id,
        {"type": "checkin_invite", "session_id": session_id, "scenario": req.scenario},
    )

    return StartCheckinResponse(
        session_id=session_id,
        user_id=req.user_id,
        push_sent=result.sent,
        push_dry_run=result.dry_run,
        live_delivered=live_delivered,
        detail=f"{result.detail}; live_delivered={live_delivered}",
    )


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
            logging.info("mock /ws/audio %s heard: %r", session_id, data.get("text"))
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
