"""Request/response and storage models."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, ConfigDict


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
    # Friendly name the patient entered in the app; used for VERA's greeting.
    display_name: str = ""


class CompleteCheckinRequest(BaseModel):
    """Sent by the app when a check-in ends; optional self-reported urgency."""

    urgency: Optional[Literal["routine", "soon", "urgent", "unsure"]] = None
    state: Optional[Literal["completed", "declined", "withdrawn", "interrupted", "escalated"]] = None


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
    empathy: bool = False   # optional empathetic acknowledgments (DRAFT)
    caregiver_consent: bool = False
    patient_id: Optional[str] = None
    stroke_type: Literal["ischemic", "hemorrhagic", "unknown"] = "unknown"
    rate: Optional[float] = Field(default=None, ge=0.5, le=1.5)
    use_preferred_contact: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


class EnrollmentRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    patient_id: Optional[str] = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    role: Literal["survivor", "caregiver"] = "survivor"
    caregiver_consent: bool = False
    stroke_type: Literal["ischemic", "hemorrhagic", "unknown"] = "unknown"
    readiness_note: Optional[str] = Field(default=None, max_length=2000)


class RedeemRequest(BaseModel):
    code: str = Field(min_length=20, max_length=200)


class CommunicationPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    communication_difficulty: Literal["not_recorded", "no", "yes", "unsure"] = "not_recorded"
    support_preference: Literal["independent", "helper", "staff", "unsure"] = "independent"
    text_only: bool = False
    speech_rate: float = Field(default=0.85, ge=0.6, le=1.2)
    manual_finish: bool = True
    review_before_sending: bool = True
    silence_seconds: int = Field(default=8, ge=3, le=30)


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    preferences: CommunicationPreferences


class ReadinessUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    readiness: Literal["not_reviewed", "ready", "with_support", "paused"]
    reassess_on: Optional[date] = None
    note: str = Field(default="", max_length=2000)


class ContactHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_contact_version: int = Field(ge=0)
    target_user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    agreement_confirmed: bool = False
    note: str = Field(min_length=1, max_length=2000)


class RecordingConsentRequest(BaseModel):
    accepted: bool


class NoteRequest(BaseModel):
    text: str


class TriageActionRequest(BaseModel):
    """Optional note attached when acknowledging/resolving a check-in."""
    note: Optional[str] = None
    owner: Optional[str] = Field(default=None, max_length=128)


class OutcomeEvent(BaseModel):
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    version: int = Field(ge=1)
    summary: dict


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    user_id: Optional[str] = None
    request_id: Optional[str] = Field(default=None, max_length=59, pattern=r"^[A-Za-z0-9_-]+$")
    share_with_team: bool = False
    callback_requested: bool = False


class AdminSettingsRequest(BaseModel):
    """Non-secret runtime settings editable from the admin page. All optional;
    only provided fields are updated. The SMTP password is intentionally absent —
    it stays an environment secret."""
    alerts_enabled: Optional[bool] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_use_tls: Optional[bool] = None
    smtp_user: Optional[str] = None
    alert_email_from: Optional[str] = None
    alert_email_to: Optional[str] = None
    console_base_url: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class StartCheckinResponse(BaseModel):
    session_id: str
    user_id: str
    push_sent: bool
    push_dry_run: bool
    live_delivered: int = 0
    detail: str = ""
