"""APNs sender (token-based / .p8 auth key, HTTP/2).

Design notes:
- DRY_RUN=true logs the payload instead of sending — no Apple account needed,
  so dev and CI work offline. This is the default.
- For the beta we send a normal *alert* push ("time for your check-in"). The
  session_id rides in the custom payload so the app knows which check-in to join.
- Upgrading to CallKit later means: switch to a VoIP topic (bundleid.voip),
  send to the PushKit token, and set apns-push-type: voip. The structure below
  is deliberately isolated so that change is small.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import httpx
import jwt

from .config import Settings

logger = logging.getLogger("kura.apns")


@dataclass
class PushResult:
    sent: bool
    dry_run: bool
    status_code: int | None = None
    detail: str = ""


class APNsClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._jwt_cache: tuple[float, str] | None = None

    # --- auth token (cached ~50 min; Apple allows up to 60) ----------------
    def _auth_token(self) -> str:
        now = time.time()
        if self._jwt_cache and now - self._jwt_cache[0] < 50 * 60:
            return self._jwt_cache[1]

        with open(self._s.apns_auth_key_path, "r") as f:
            key = f.read()
        token = jwt.encode(
            {"iss": self._s.apns_team_id, "iat": int(now)},
            key,
            algorithm="ES256",
            headers={"kid": self._s.apns_key_id},
        )
        self._jwt_cache = (now, token)
        return token

    def _payload(self, *, session_id: str, scenario: str) -> dict:
        return {
            "aps": {
                "alert": {
                    "title": "VERA check-in",
                    "body": "Your care team has a quick check-in ready. Tap to start.",
                },
                "sound": "default",
                # category lets the app render Accept/Decline actions if desired
                "category": "VERA_CHECKIN",
            },
            # custom keys the app reads to join the right session
            "session_id": session_id,
            "scenario": scenario,
            "type": "checkin_invite",
        }

    async def send_checkin(
        self, *, push_token: str, session_id: str, scenario: str
    ) -> PushResult:
        payload = self._payload(session_id=session_id, scenario=scenario)

        if self._s.dry_run:
            logger.info(
                "[DRY_RUN] APNs push to %s…: %s",
                push_token[:8],
                json.dumps(payload),
            )
            return PushResult(sent=True, dry_run=True, detail="dry-run (not sent)")

        url = f"{self._s.apns_host}/3/device/{push_token}"
        headers = {
            "authorization": f"bearer {self._auth_token()}",
            "apns-topic": self._s.apns_bundle_id,
            "apns-push-type": "alert",
            "apns-priority": "10",
        }
        # APNs requires HTTP/2.
        async with httpx.AsyncClient(http2=True, timeout=15) as client:
            resp = await client.post(url, headers=headers, json=payload)
        ok = resp.status_code == 200
        detail = "ok" if ok else f"APNs error: {resp.text}"
        if not ok:
            logger.warning("APNs send failed (%s): %s", resp.status_code, resp.text)
        return PushResult(
            sent=ok, dry_run=False, status_code=resp.status_code, detail=detail
        )
