# Deploying the push-service to Azure

Goal: run the push-service on the internet (not your Mac's LAN) so the phone
reaches it from anywhere — and you can keep building on it.

## Tier: Free (F1) is fine

The app delivers check-ins by **polling** (`GET /v1/checkins/pending/{user_id}`),
which is plain HTTP — so the **free F1 tier works** (no WebSockets needed). You
can host this on your own Azure account, separate from VERA.

> The `/v1/notify` WebSocket and `/ws/audio` mock still exist for local dev, but
> the deployed app uses polling and never needs them.

## Setup (auto-deploy from GitHub) — one time

CI/CD is wired in `.github/workflows/deploy-push-service.yml`: after setup,
every push to `main` that touches `push-service/**` runs the tests and deploys.

### 1. Create the Web App (az CLI)

```bash
az login

# pick names you like
RG=kura-rg
PLAN=kura-plan
APP=kura-push        # must be globally unique; becomes <APP>.azurewebsites.net

az group create --name $RG --location eastus

# FREE plan (F1) — no cost
az appservice plan create --resource-group $RG --name $PLAN --sku F1 --is-linux

az webapp create --resource-group $RG --plan $PLAN --name $APP --runtime "PYTHON:3.11"

# build on deploy + startup command
az webapp config set --resource-group $RG --name $APP \
  --startup-file "python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"

# env vars (replace the local .env)
az webapp config appsettings set --resource-group $RG --name $APP --settings \
  SCM_DO_BUILD_DURING_DEPLOYMENT="true" \
  VERA_API_BASE="https://vera-cloud-app-dbhrdyfbg8cyhfam.eastus2-01.azurewebsites.net" \
  DRY_RUN="true" \
  PROVIDER_API_KEY="<pick-a-secret>" \
  DEVICE_STORE_PATH="/home/data/devices.json"
```

> Prefer clicking? In the **Azure Portal**: Create a resource → **Web App** →
> Runtime **Python 3.11**, Linux, Pricing plan **Free F1**. Then in the app's
> **Configuration**: add the same Application settings above and set the
> **Startup Command** to the uvicorn line. Then **Get publish profile** (step 2).

### 2. Connect GitHub Actions to it

```bash
# download the publish profile (the deploy credential)
az webapp deployment list-publishing-profiles \
  --resource-group $RG --name $APP --xml > kura-push.publishsettings
```

- In GitHub → your **Kura** repo → **Settings → Secrets and variables → Actions
  → New repository secret**: name it **`AZURE_WEBAPP_PUBLISH_PROFILE`**, paste the
  entire contents of that `kura-push.publishsettings` file. **Delete the local
  file afterward** (it's a credential).
- If you named the app something other than `kura-push`, edit
  `AZURE_WEBAPP_NAME` at the top of `.github/workflows/deploy-push-service.yml`.

### 3. Deploy

```bash
git add -A && git commit -m "Deploy push-service to Azure" && git push
```

Watch it in the repo's **Actions** tab. Your service lands at
`https://kura-push.azurewebsites.net`. From then on, every `git push` that
changes `push-service/**` redeploys automatically.

## After deploy

1. **Protect it.** It's now public, so set `PROVIDER_API_KEY` (above). The
   provider console has a "Provider key" box — enter the same value there. The
   app's device-registration endpoint stays open (the app needs it).
2. **Point the app at it.** In `ios/Sources/Config.swift`:
   ```swift
   static let pushServiceBaseURL = URL(string: "https://kura-push.azurewebsites.net")!
   ```
   Rebuild. The phone now reaches the push-service over the internet (even on
   cellular) — no more same-Wi-Fi requirement. (Because it's HTTPS, you can also
   drop the dev ATS exception later.)
3. **Console** is at `https://kura-push.azurewebsites.net/`.

## Notes

- **Free F1 limits:** F1 sleeps when idle (a first request after idle has a
  cold-start delay of a few seconds) and has a daily CPU quota. Fine for a
  showcase; move to B1+ for always-on / production.
- **Persistence:** `DEVICE_STORE_PATH=/home/data/devices.json` keeps device
  registrations across restarts (`/home` persists on App Service). The pending
  check-in queue is in-memory. Swap both for a real DB (Postgres/Cosmos) later.
- **Single instance:** keep at 1 instance — the device registry and pending
  queue are in-process. Scale-out needs shared state (e.g. Redis) first.
- **Logs:** `az webapp log tail --resource-group $RG --name $APP`.
