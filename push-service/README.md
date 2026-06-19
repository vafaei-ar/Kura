# Kura push-service

FastAPI service that (1) lets the iOS app register a patient's push token and
(2) lets a provider trigger a voice check-in, which creates a VERA-cloud session
and pushes it to the phone.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # DRY_RUN=true by default — no Apple certs needed
uvicorn app.main:app --reload
pytest                        # all tests run in dry-run mode, offline
```

## Endpoints

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET  | `/health` | — | liveness + config summary |
| POST | `/v1/devices/register` | iOS app | store `user_id → push_token` |
| GET  | `/v1/devices/{user_id}` | debug | inspect a device (token masked) |
| POST | `/v1/checkins/start` | provider console | create VERA session + push to phone |

### Try it (dry-run)

```bash
# register a device
curl -s localhost:8000/v1/devices/register \
  -H 'content-type: application/json' \
  -d '{"user_id":"patient-001","push_token":"abcdef0123456789"}'

# provider triggers a check-in (push is logged, not sent, in dry-run)
curl -s localhost:8000/v1/checkins/start \
  -H 'content-type: application/json' \
  -d '{"user_id":"patient-001","scenario":"guided.yml"}'
```

## Going live (leaving dry-run)

1. Apple Developer → create an **APNs Auth Key** (`.p8`). Note its **Key ID**
   and your **Team ID**. Put the `.p8` somewhere outside git.
2. Fill `.env`: `DRY_RUN=false`, `APNS_TEAM_ID`, `APNS_KEY_ID`,
   `APNS_BUNDLE_ID` (your app id), `APNS_AUTH_KEY_PATH`.
   Keep `APNS_USE_SANDBOX=true` for development/TestFlight builds.
3. Set `VERA_API_BASE` to your VERA-cloud URL so real sessions are created.
4. Set `PROVIDER_API_KEY` and have the console send `X-Provider-Key`.

## Notes / next steps

- The device registry is an in-memory/JSON store — swap for Postgres/Cosmos
  before production (see `app/store.py`).
- Provider auth is a single shared secret for the beta — replace with real
  provider identities/SSO later.
- The push payload (`app/apns.py`) is a normal **alert**. The structure is
  isolated so a future **CallKit/VoIP** upgrade is a small, contained change.
