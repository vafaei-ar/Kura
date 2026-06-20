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
        role: str = "survivor",
    ) -> str:
        """Return a session_id for a new check-in."""
        if not self.configured:
            # Local stub: VERA not wired up yet.
            return str(uuid.uuid4())

        url = self._s.vera_api_base.rstrip("/") + "/session/start"
        headers = {}
        if self._s.vera_api_key:
            headers["Authorization"] = f"Bearer {self._s.vera_api_key}"

        # Matches VERA-cloud SessionStartRequest (api/main.py): patient_name and
        # role are required; patient_id is the optional PATID for history lookup.
        payload = {
            "patient_name": patient_name or user_id,
            "role": role,
            "patient_id": user_id,
            "honorific": honorific,
            "scenario": scenario,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        session_id = data.get("session_id")
        if not session_id:
            raise RuntimeError(f"VERA /session/start returned no session_id: {data}")
        return session_id

    async def clinician_summary(self, session_id: str) -> dict | None:
        """Fetch VERA's clinician summary (flags/tiers) for a finished check-in.

        Returns None if VERA isn't configured or has no outcome yet.
        """
        if not self.configured:
            return None
        url = self._s.vera_api_base.rstrip("/") + f"/api/session/{session_id}/clinician-summary"
        headers = {}
        if self._s.vera_api_key:
            headers["Authorization"] = f"Bearer {self._s.vera_api_key}"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return None
            return resp.json()
