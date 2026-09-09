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
        self.session_token = None

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
        empathy: bool = False,
        caregiver_consent: bool = False,
        patient_id: str | None = None,
        stroke_type: str = "unknown",
        rate: float | None = None,
        communication_preferences: dict | None = None,
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
            "respondent_id": user_id,
            "role": role,
            "patient_id": patient_id,
            "caregiver_consent": caregiver_consent,
            "stroke_type": stroke_type,
            "rate": rate,
            "communication_preferences": communication_preferences,
            "honorific": honorific,
            "scenario": scenario,
            "empathy": empathy,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        session_id = data.get("session_id")
        self.session_token = data.get("session_token")
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

    async def answer_receipt(self, session_id: str, payload: dict) -> dict:
        if not self.configured:
            raise ValueError("Answer recovery requires VERA")
        headers = {"Authorization": "Bearer " + self._s.vera_api_key} if self._s.vera_api_key else {}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(self._s.vera_api_base.rstrip("/") + f"/api/session/{session_id}/answer-receipt",
                                         json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    async def stop_session(self, session_id: str) -> dict:
        if not self.configured:
            return {"session_id": session_id, "state": "declined", "has_priority": False}
        url = self._s.vera_api_base.rstrip("/") + f"/api/session/{session_id}/stop"
        headers = {"Authorization": f"Bearer {self._s.vera_api_key}"} if self._s.vera_api_key else {}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json={"state": "declined"}, headers=headers)
            response.raise_for_status()
            return response.json()

    async def capabilities(self):
        if not self.configured:
            return {"original_audio": False}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(self._s.vera_api_base.rstrip("/") + "/api/capabilities")
            response.raise_for_status()
            return response.json()

    async def recording_consent(self, session_id, accepted):
        if not self.configured:
            raise ValueError("Original recording is unavailable")
        headers = {"Authorization": "Bearer " + self._s.vera_api_key}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(self._s.vera_api_base.rstrip("/") + f"/api/session/{session_id}/recording-consent",
                headers=headers, json={"accepted": accepted})
            response.raise_for_status()
            return response.json()

    async def upload_audio(self, session_id, clip_id, content, partial):
        if not self.configured:
            return 503, {"stored": False}
        headers = {"Authorization": "Bearer " + self._s.vera_api_key, "Content-Type": "audio/wav",
                   "X-Audio-Partial": "true" if partial else "false"}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(self._s.vera_api_base.rstrip("/") + f"/api/session/{session_id}/audio/{clip_id}",
                    headers=headers, content=content)
                return response.status_code, response.json()
        except (httpx.HTTPError, ValueError):
            return 503, {"stored": False}

    async def original_audio(self, session_id, clip_id, reviewer):
        if not self.configured:
            return None
        headers = {"Authorization": "Bearer " + self._s.vera_api_key, "X-Reviewer": reviewer}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(self._s.vera_api_base.rstrip("/") + f"/api/session/{session_id}/audio/{clip_id}", headers=headers)
            if response.status_code != 200:
                return None
            return response.content

    async def record_urgency(self, session_id: str, urgency: str, role: str = "survivor") -> None:
        """Record the patient's self-reported urgency (routine|soon|urgent) in VERA.
        Advisory only — never overrides automatic flagging (VERA enforces that)."""
        if not self.configured:
            return
        url = self._s.vera_api_base.rstrip("/") + f"/api/session/{session_id}/urgency"
        headers = {}
        if self._s.vera_api_key:
            headers["Authorization"] = f"Bearer {self._s.vera_api_key}"
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json={"urgency": urgency, "role": role}, headers=headers)
            response.raise_for_status()

    async def get_resources(self, region: str | None = None, need: str | None = None) -> dict | None:
        """Curated local resources (transportation, support, ...) from VERA's
        info-only directory. Returns None if VERA isn't configured."""
        if not self.configured:
            return None
        url = self._s.vera_api_base.rstrip("/") + "/api/resources"
        params = {}
        if region:
            params["region"] = region
        if need:
            params["need"] = need
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            return resp.json() if resp.status_code == 200 else None

    async def get_resource_regions(self) -> dict | None:
        if not self.configured:
            return None
        url = self._s.vera_api_base.rstrip("/") + "/api/resource-regions"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            return resp.json() if resp.status_code == 200 else None

    async def ask(self, question: str, session_id: str | None = None,
                  share_with_team: bool = False, callback_requested: bool = False) -> dict | None:
        """Ask-VERA (retrieval-only). Returns VERA's answer/refusal/safety dict,
        or None if VERA isn't configured or Ask-VERA is disabled there (403)."""
        if not self.configured:
            return None
        url = self._s.vera_api_base.rstrip("/") + "/api/ask"
        headers = {}
        if self._s.vera_api_key:
            headers["Authorization"] = f"Bearer {self._s.vera_api_key}"
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, json={"question": question, "session_id": session_id,
                "share_with_team": share_with_team, "callback_requested": callback_requested}, headers=headers)
            return resp.json() if resp.status_code == 200 else None
