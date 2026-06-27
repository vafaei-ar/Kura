"""Runtime settings overrides, editable from the admin page.

Non-secret settings (alert recipients, SMTP host/port, toggle, ...) are stored in
the `settings` table and OVERLAY the env-based defaults at runtime, so an admin can
change them without a redeploy. The env values remain the bootstrap/fallback.

Secrets stay in the environment: the SMTP password is never read from, written to,
or returned by this module.
"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import Setting as SettingRow

# Editable, NON-SECRET keys and their types. Each maps to a field on Settings.
EDITABLE: Dict[str, type] = {
    "alerts_enabled": bool,
    "smtp_host": str,
    "smtp_port": int,
    "smtp_use_tls": bool,
    "smtp_user": str,
    "alert_email_from": str,
    "alert_email_to": str,
    "console_base_url": str,
}


def _coerce(key: str, raw: str) -> Any:
    typ = EDITABLE[key]
    if typ is bool:
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if typ is int:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0
    return raw


def get_overrides(db: Session) -> Dict[str, Any]:
    """Return the typed override values currently stored (only known keys)."""
    rows = db.execute(select(SettingRow)).scalars().all()
    out: Dict[str, Any] = {}
    for r in rows:
        if r.key in EDITABLE:
            out[r.key] = _coerce(r.key, r.value)
    return out


def set_overrides(db: Session, data: Dict[str, Any], updated_by: str | None = None) -> None:
    """Upsert any provided editable keys. Unknown keys are ignored; secrets can't
    be set here because they aren't in EDITABLE."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for key, val in (data or {}).items():
        if key not in EDITABLE:
            continue
        if val is None:
            continue
        if isinstance(val, bool):
            sval = "true" if val else "false"
        else:
            sval = str(val)
        row = db.get(SettingRow, key)
        if row is None:
            row = SettingRow(key=key, value=sval, updated_at=now, updated_by=updated_by)
            db.add(row)
        else:
            row.value = sval
            row.updated_at = now
            row.updated_by = updated_by
    db.commit()


def effective_settings(db: Session, env: Settings) -> Settings:
    """Env settings with DB overrides applied (password and other secrets kept
    from env). Use this anywhere alert behavior is read."""
    overrides = get_overrides(db)
    if not overrides:
        return env
    return env.model_copy(update=overrides)


def admin_view(db: Session, env: Settings) -> Dict[str, Any]:
    """Current effective values for the admin form, plus read-only status. No
    secrets are returned — only whether the SMTP password is configured in env."""
    eff = effective_settings(db, env)
    view = {k: getattr(eff, k) for k in EDITABLE}
    view["smtp_password_configured"] = bool(env.smtp_password)
    view["email_alerts_ready"] = eff.alerts_enabled and eff.email_alerts_configured and bool(env.smtp_password)
    return view
