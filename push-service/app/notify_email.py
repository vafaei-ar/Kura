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


def send_alert(settings: Settings, user_id: str, session_id: str, *, tier: int) -> bool:
    """Send the alert email. Returns True if actually sent, False if skipped
    (not configured) or on error. Never raises."""
    if not settings.email_alerts_configured:
        logger.info("Email alert skipped (SMTP not configured) for %s tier %s", user_id, tier)
        return False
    subject, body = build_alert(user_id, session_id, tier=tier,
                                console_base_url=settings.console_base_url)
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
        logger.info("Sent Tier-%s alert email for %s to %s", tier, user_id,
                    settings.alert_recipients)
        return True
    except Exception as exc:  # pragma: no cover - network/SMTP errors
        logger.error("Failed to send alert email for %s: %s", user_id, exc)
        return False
