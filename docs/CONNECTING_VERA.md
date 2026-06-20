# Connecting Kura to real VERA-cloud

By default Kura runs against the push-service's **mock** `/ws/audio` (a scripted
check-in, no Azure). To use the real Azure neural voices and the real
dialog/flagging engine, point Kura at a running **VERA-cloud** instance.

## How the pieces connect

```
provider ──▶ push-service ──POST /session/start──▶ VERA-cloud   (creates session_id)
                                                      ▲
iPhone ──────────── wss /ws/audio/{session_id} ───────┘          (same VERA process)
```

The **same** VERA-cloud process must both create the session and serve
`/ws/audio` — VERA keeps sessions in memory (`active_sessions`). So the
push-service's `VERA_API_BASE` and the app's `Config.veraBaseURL` must point at
the **same** VERA host.

## Steps

### 1. Run VERA-cloud (with Azure credentials)
In the VERA-cloud repo, set its Azure env (OpenAI + Speech keys — see its
`env.example` / README) and run the API so it listens on the LAN:

```bash
# in VERA-cloud/
uvicorn api.main:app --host 0.0.0.0 --port 8001
```

Use a different port than the push-service (8000). Without Azure keys VERA still
works but sends text-only (no `audio_data`); the app then reads questions aloud
with on-device TTS.

### 2. Point the push-service at VERA
In `push-service/.env`:

```
VERA_API_BASE=http://10.0.0.207:8001     # your VERA host:port
# VERA_API_KEY=...                        # only if VERA requires auth
```

Restart the push-service. Now `/v1/checkins/start` creates a **real** VERA
session instead of a stub UUID. (The request maps `user_id → patient_id`,
`role` defaults to `survivor`; see `app/vera_client.py`.)

### 3. Point the app at VERA
In `ios/Sources/Config.swift`:

```swift
static let veraBaseURL = URL(string: "http://10.0.0.207:8001")!   // VERA, not the push-service
```

Rebuild. The app now opens `/ws/audio` on VERA and plays the **Azure MP3**
audio; recognition (`SFSpeechRecognizer`) and the `text_input` protocol are
unchanged.

### 4. Trigger as before
```bash
curl -X POST http://localhost:8000/v1/checkins/start \
  -H 'content-type: application/json' -d '{"user_id":"patient-001"}'
```

The phone is invited over the live notify socket, joins VERA's `/ws/audio`, and
runs the real clinical check-in with Azure voices and BE-FAST flagging.

## Notes & gotchas

- **Networking:** the phone must reach the VERA host. On a Mac, bind `0.0.0.0`
  and use the LAN IP; the app's dev ATS exception already allows cleartext HTTP.
  Use `wss://`/HTTPS for any real deployment.
- **patient_id:** VERA uses it to load synthetic CDM history for context. Map
  your enrollment `user_id` to a real PATID when that matters; with an unknown
  id VERA runs in generic mode (Tier-1 BE-FAST still fires).
- **Two hosts, one VERA:** don't run two VERA instances behind a load balancer
  for this yet — sessions are in-process. Single instance for the beta.
- **Clinical logic stays in VERA-cloud** — Kura adds none. All flagging and
  escalation remain VERA's (DRAFT, pending clinician sign-off).
