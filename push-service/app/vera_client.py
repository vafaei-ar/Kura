"""Client for VERA-cloud's session-initiation contract.

VERA-cloud exposes POST /session/start (see VERA-cloud api/main.py). We call it
to mint a session_id for the check-in, then hand that session_id to the phone
via push. If VERA_API_BASE is unset, we stub a local UUID so the rest of the
flow is testable before VERA-cloud is wired up.
"""
from __future__ import annotations

import uuid

import httpx

from .config import Settings


class VeraClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    @property
    def configured(self) -> bool:
        return bool(self._s.vera_api_base)

    async def start_session(
        self,
        *,
        user_id: str,
        scenario: str,
        patient_name: str,
        honorific: str,
    ) -> str:
        """Return a session_id for a new check-in."""
        if not self.configured:
            # Local stub: VERA not wired up yet.
            return str(uuid.uuid4())

        url = self._s.vera_api_base.rstrip("/") + "/session/start"
        headers = {}
        if self._s.vera_api_key:
            headers["Authorization"] = f"Bearer {self._s.vera_api_key}"

        payload = {
            "patient_id": user_id,
            "scenario": scenario,
            "patient_name": patient_name,
            "honorific": honorific,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        session_id = data.get("session_id")
        if not session_id:
            raise RuntimeError(f"VERA /session/start returned no session_id: {data}")
        return session_id
