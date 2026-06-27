"""Emergency (Tier-1) email alerts — stdlib SMTP, no third-party deps.

Sends a short email to the on-call clinician(s) when a check-in is flagged as a
Tier-1 (emergency) red flag. If SMTP isn't configured the functions no-op (and
log), so the feature can ship dark and be armed later via Azure app settings.

Patient-safety note: the email intentionally carries MINIMAL clinical detail —
the participant id, the tier, and a link back to the console — so PHI isn't
scattered into inboxes. The clinician opens the console to see the full summary.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

from .config import Settings

logger = logging.getLogger(__name__)


def build_alert(user_id: str, session_id: str, *, tier: int,
                console_base_url: str = "") -> tuple[str, str]:
    """Return (subject, body) for an emergency alert email."""
    label = "EMERGENCY red flag" if tier == 1 else "Priority flag"
    subject = f"[Lion AI Navigator] {label} — patient {user_id}"
    link = ""
    if console_base_url:
        link = f"\nOpen the console: {console_base_url.rstrip('/')}/console\n"
    body = (
        f"A check-in was flagged as a {label}.\n\n"
        f"Patient (participant id): {user_id}\n"
        f"Tier: {tier}\n"
        f"Check-in id: {session_id}\n"
        f"{link}\n"
        "Sign in to the provider console to review the full summary and triage.\n\n"
        "— Lion AI Navigator (Penn State Health). Automated alert; do not reply.\n"
        "DRAFT clinical workflow — pending clinician sign-off."
    )
    return subject, body


def _send(settings: Settings, subject: str, body: str) -> tuple[bool, str]:
    """Low-level send. Returns (ok, detail). Never raises."""
    if not settings.alerts_enabled:
        return False, "alerts disabled"
    if not settings.email_alerts_configured:
        return False, "SMTP not configured"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.alert_email_from
    msg["To"] = ", ".join(settings.alert_recipients)
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return True, f"sent to {', '.join(settings.alert_recipients)}"
    except Exception as exc:  # pragma: no cover - network/SMTP errors
        logger.error("Email send failed: %s", exc)
        return False, f"send failed: {exc}"


def send_alert(settings: Settings, user_id: str, session_id: str, *, tier: int) -> bool:
    """Send an emergency alert email. Returns True if actually sent."""
    subject, body = build_alert(user_id, session_id, tier=tier,
                                console_base_url=settings.console_base_url)
    ok, detail = _send(settings, subject, body)
    logger.info("Tier-%s alert for %s: %s", tier, user_id, detail)
    return ok


def send_test(settings: Settings) -> tuple[bool, str]:
    """Send a test alert so an admin can verify the email config. Returns
    (ok, human-readable detail)."""
    subject = "[Lion AI Navigator] Test alert"
    body = (
        "This is a test of the Lion AI Navigator emergency-alert email.\n\n"
        "If you received this, alert delivery is configured correctly.\n\n"
        "— Penn State Health · automated test, do not reply."
    )
    return _send(settings, subject, body)
