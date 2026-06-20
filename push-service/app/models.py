"""Request/response and storage models."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Device registration -------------------------------------------------

class DeviceRegistration(BaseModel):
    """Sent by the iOS app after it obtains a push token."""

    user_id: str = Field(..., description="Stable patient/participant identifier")
    push_token: str = Field(..., description="APNs device token (hex)")
    platform: Literal["ios"] = "ios"
    # 'alert' = normal user-facing notification (our beta default).
    # 'voip'  = PushKit token, reserved for a future CallKit upgrade.
    token_type: Literal["alert", "voip"] = "alert"
    app_version: Optional[str] = None
    # Participant role, declared at registration: survivor | caregiver.
    role: str = "survivor"


class CompleteCheckinRequest(BaseModel):
    """Sent by the app when a check-in ends; optional self-reported urgency."""

    urgency: Optional[str] = None  # routine | soon | urgent


class Device(DeviceRegistration):
    registered_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# --- Provider-triggered check-in ----------------------------------------

class StartCheckinRequest(BaseModel):
    """Sent by the provider console to trigger a check-in for a patient."""

    user_id: str
    scenario: str = "guided.yml"
    patient_name: str = ""
    honorific: str = ""
    role: str = "survivor"  # survivor | caregiver | clinician (VERA role track)


class StartCheckinResponse(BaseModel):
    session_id: str
    user_id: str
    push_sent: bool
    push_dry_run: bool
    live_delivered: int = 0
    detail: str = ""
