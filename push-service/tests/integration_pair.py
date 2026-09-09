"""Run both actual APIs in isolated processes; no Apple/Azure/SMTP/user data.

Run from push-service: .venv/bin/python tests/integration_pair.py
Requires the sibling VERA-cloud/.venv established by the working plan.
"""
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import wave

import httpx
import websockets

KURA = Path(__file__).resolve().parents[2]
VERA = KURA.parent / "VERA-cloud"
SERVICE_KEY = "synthetic-service-key-not-a-real-credential"
PROVIDER_KEY = "synthetic-provider-key-not-a-real-credential"
EVENT_KEY = "synthetic-event-key-not-a-real-credential"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def launch(python, module, cwd, env, port, log):
    return subprocess.Popen([str(python), "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port), "--ws-max-size", "16000000"],
                            cwd=cwd, env=env, stdout=log, stderr=log)


def wait_ready(url, process):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Synthetic service failed to start")
        try:
            if httpx.get(url + "/health", timeout=1, trust_env=False).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Synthetic service readiness timeout")


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 160)
    return output.getvalue()


async def check_pair(kura_url, vera_url):
    async with httpx.AsyncClient(base_url=kura_url, timeout=20, trust_env=False) as client:
        provider = {"X-Provider-Key": PROVIDER_KEY}
        response = await client.post("/v1/enrollments", headers=provider, json={
            "user_id": "synthetic-caregiver", "patient_id": "synthetic-patient", "role": "caregiver", "caregiver_consent": True})
        response.raise_for_status()
        code = response.json()["enrollment_code"]
        response = await client.post("/v1/enrollments/redeem", json={"code": code})
        response.raise_for_status()
        participant = {"Authorization": "Bearer " + response.json()["token"]}
        response = await client.post("/v1/devices/register", headers=participant, json={
            "user_id": "synthetic-caregiver", "push_token": "SYNTHETIC-ONLY", "role": "survivor"})
        assert response.json()["role"] == "caregiver"
        assert (await client.get("/v1/checkins/pending/someone-else", headers=participant)).status_code == 403
        print("PASS enrollment, authoritative caregiver role, cross-account denial", flush=True)

        current = (await client.get("/v1/participants/me/preferences", headers=participant)).json()
        preferences = {**current["preferences"], "speech_rate": 0.7, "communication_difficulty": "yes"}
        response = await client.put("/v1/participants/me/preferences", headers=participant,
            json={"expected_version": current["version"], "preferences": preferences})
        response.raise_for_status()

        response = await client.post("/v1/checkins/start", headers=provider, json={"user_id": "synthetic-caregiver"})
        response.raise_for_status()
        sid = response.json()["session_id"]
        token = (await client.get(f"/v1/checkins/{sid}/connection", headers=participant)).json()["session_token"]
        assert token
        response = await client.post(f"/v1/checkins/{sid}/recording-consent", headers=participant, json={"accepted": True})
        response.raise_for_status()
        uri = vera_url.replace("http://", "ws://") + "/ws/audio/" + sid
        async with websockets.connect(uri, additional_headers={"Authorization": "Bearer " + token}, proxy=None) as ws:
            greeting = json.loads(await ws.recv())
            assert "Say yes or no" in greeting["text"] and greeting["speech_rate"] == 0.7
            assert greeting["answer_recovery"] == 1
            consent = {"type": "text_input", "text": "yes", "message_id": "consent",
                       "expected_context": greeting["answer_context"], "request_receipt": True}
            missing = await client.post(f"/v1/checkins/{sid}/answer-receipt", headers=participant, json=consent)
            missing.raise_for_status()
            assert missing.json()["can_retry"] and not missing.json()["saved"]
            await ws.send(json.dumps(consent))
            receipt = json.loads(await ws.recv())
            assert receipt["type"] == "answer_receipt" and receipt["saved"]
            assert json.loads(await ws.recv())["consent"] == "accepted"
            emergency = {"type": "text_input", "text": "my face is drooping", "message_id": "original1",
                         "expects_audio": True, "expected_context": receipt["answer_context"], "request_receipt": True}
            await ws.send(json.dumps(emergency))
            terminal = json.loads(await ws.recv())
            assert terminal["type"] == "emergency_alert" and terminal["saved"]
            assert terminal["message_id"] == "original1"
        # Recover a lost terminal receipt through the owner-authenticated broker;
        # do not reopen a terminal socket or resend the answer with a new ID.
        recovered = await client.post(f"/v1/checkins/{sid}/answer-receipt", headers=participant, json=emergency)
        recovered.raise_for_status()
        assert recovered.json()["saved"] and recovered.json()["state"] == "escalated"
        assert not recovered.json()["can_retry"]
        print("PASS question-bound recovery and terminal lost-receipt reconciliation through both APIs", flush=True)
        # Deliberately upload AFTER emergency guidance; audio is never a prerequisite.
        upload = await client.post(f"/v1/checkins/{sid}/audio/original1", headers=participant, content=wav())
        upload.raise_for_status()
        assert upload.json()["stored"]
        # Do NOT fetch a summary/complete here: that would bypass the outbox
        # and conceal a broken automatic-delivery path.
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            rows = (await client.get("/v1/checkins", headers=provider)).json()
            row = next(row for row in rows if row["session_id"] == sid)
            if row["has_priority"]:
                break
            await asyncio.sleep(0.2)
        assert row["has_priority"] and row["status"] == "escalated"
        assert row["alert_state"] == "failed"  # SMTP deliberately disabled
        print("PASS immediate emergency event + automatic durable outbox + visible failed delivery", flush=True)

        assert (await client.get(f"/v1/checkins/{sid}/audio/original1", headers=participant)).status_code == 401
        audio = await client.get(f"/v1/checkins/{sid}/audio/original1", headers=provider)
        audio.raise_for_status()
        assert audio.content == wav()
        print("PASS original audio consent, persistence, and clinician-only playback", flush=True)

        response = await client.post(f"/v1/checkins/{sid}/complete", headers=participant, json={"urgency": "routine"})
        response.raise_for_status()
        assert response.json()["has_priority"] and response.json()["state"] == "escalated"
        print("PASS patient urgency cannot suppress the emergency outcome", flush=True)

        response = await client.post("/v1/ask", headers=participant, json={
            "user_id": "synthetic-caregiver", "request_id": "synthetic-question", "question": "can I drive?",
            "share_with_team": True, "callback_requested": True})
        response.raise_for_status()
        assert response.json()["saved"]
        rows = (await client.get("/v1/checkins?unassigned=true", headers=provider)).json()
        assert any(row["session_id"] == "ask-synthetic-question" for row in rows)
        print("PASS Ask question and callback reach the unassigned worklist", flush=True)

        response = await client.post("/v1/enrollments", headers=provider,
            json={"user_id": "synthetic-survivor", "patient_id": "synthetic-patient", "role": "survivor"})
        response.raise_for_status()
        response = await client.post("/v1/enrollments/redeem", json={"code": response.json()["enrollment_code"]})
        response.raise_for_status()
        survivor = {"Authorization": "Bearer " + response.json()["token"]}
        response = await client.post("/v1/devices/register", headers=survivor,
            json={"user_id": "synthetic-survivor", "push_token": "SYNTHETIC-SURVIVOR-ONLY"})
        response.raise_for_status()
        response = await client.put("/v1/enrollments/synthetic-survivor/readiness", headers=provider,
            json={"expected_version": 0, "readiness": "ready", "note": "Synthetic agreed independent use"})
        response.raise_for_status()
        response = await client.post("/v1/enrollments/synthetic-caregiver/handoff", headers=provider,
            json={"expected_contact_version": 0, "target_user_id": "synthetic-survivor",
                  "agreement_confirmed": True, "note": "Synthetic explicit future-contact agreement"})
        response.raise_for_status()
        response = await client.post("/v1/checkins/start", headers=provider,
            json={"user_id": "synthetic-caregiver", "use_preferred_contact": True})
        response.raise_for_status()
        assert response.json()["user_id"] == "synthetic-survivor"
        survivor_sid = response.json()["session_id"]
        assert (await client.get(f"/v1/checkins/{survivor_sid}/connection", headers=participant)).status_code == 403
        response = await client.post(f"/v1/checkins/{survivor_sid}/decline", headers=survivor)
        response.raise_for_status()
        print("PASS preferences reach VERA; agreed handoff delivers to survivor without cross-account access", flush=True)

        response = await client.post("/v1/enrollments/synthetic-caregiver/revoke", headers=provider)
        response.raise_for_status()
        assert (await client.get(f"/v1/checkins/{sid}/connection", headers=participant)).status_code == 401
        print("PASS revocation removes participant access", flush=True)


def main():
    with tempfile.TemporaryDirectory(prefix="kura-vera-integration-") as folder:
        directory = Path(folder)
        kura_port, vera_port = free_port(), free_port()
        kura_url, vera_url = f"http://127.0.0.1:{kura_port}", f"http://127.0.0.1:{vera_port}"
        env = {**os.environ, "DEPLOYMENT_MODE": "development", "DRY_RUN": "true", "ASK_ENABLED": "true",
               "VERA_API_BASE": vera_url, "VERA_API_KEY": SERVICE_KEY, "VERA_SERVICE_KEY": SERVICE_KEY,
               "VERA_EVENT_KEY": EVENT_KEY, "KURA_EVENT_KEY": EVENT_KEY, "KURA_EVENT_URL": kura_url + "/v1/vera/events",
               "PROVIDER_API_KEY": PROVIDER_KEY, "SESSION_SECRET": "synthetic-session-secret-long-enough-for-tests",
               "DATABASE_URL": "sqlite:///" + str(directory / "kura.db"), "OUTCOMES_PATH": str(directory / "outcomes"),
               "CDM_DATA_PATH": str(directory / "no-patient-data"), "AUDIO_RECORDING_ENABLED": "true",
               "ALERTS_ENABLED": "false", "SMTP_HOST": "", "ALERT_EMAIL_FROM": "", "ALERT_EMAIL_TO": ""}
        processes = []
        with open(directory / "services.log", "w+") as log:
            try:
                processes.append(launch(VERA / ".venv/bin/python", "tests.synthetic_server:app", VERA, {**env, "PYTHONPATH": str(VERA)}, vera_port, log))
                processes.append(launch(KURA / "push-service/.venv/bin/python", "app.main:app", KURA / "push-service", env, kura_port, log))
                wait_ready(vera_url, processes[0]); wait_ready(kura_url, processes[1])
                asyncio.run(check_pair(kura_url, vera_url))
            except Exception:
                log.flush(); log.seek(0)
                print(log.read()[-12000:])  # synthetic fixtures only, no real credentials/data
                raise
            finally:
                for process in processes:
                    process.terminate()
                for process in processes:
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
    print("PASS both-repository integration; temporary synthetic data removed", flush=True)


if __name__ == "__main__":
    main()
