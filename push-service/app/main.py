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

import logging

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

    return StartCheckinResponse(
        session_id=session_id,
        user_id=req.user_id,
        push_sent=result.sent,
        push_dry_run=result.dry_run,
        detail=result.detail,
    )


@app.websocket("/ws/audio/{session_id}")
async def mock_audio_ws(websocket: WebSocket, session_id: str) -> None:
    """DEV MOCK of VERA-cloud's audio socket.

    Lets the iOS check-in screen reach a 'live' state without VERA running:
    accepts the connection, sends a 'ready' frame, and drains incoming mic
    frames. In production the app points Config.veraBaseURL at the real
    VERA-cloud and this endpoint is unused.
    """
    await websocket.accept()
    await websocket.send_json({"type": "ready", "session_id": session_id})
    frames = 0
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                frames += 1
                if frames % 50 == 0:
                    logging.info("mock /ws/audio %s: %d audio frames", session_id, frames)
    except WebSocketDisconnect:
        pass
