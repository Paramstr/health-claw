# whoop-ingest

Dev-only WHOOP data ingestion. Gets live WHOOP data into Postgres for downstream agents (OpenClaw) to consume.

## Quick start

```bash
# 1. Start Postgres
docker compose up -d

# 2. Create venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Fill in WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET, and generate APP_SECRET:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 4. Start ngrok (separate terminal)
ngrok http 8000

# 5. Run the app
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

## WHOOP developer portal setup

Set these in your WHOOP app config:
- **Redirect URI:** `https://<ngrok>/whoop/callback`
- **Webhook URL:** `https://<ngrok>/whoop/webhook`

Then approve your app: `https://<ngrok>/whoop/login`

## Endpoints

| Route | Method | Purpose |
|---|---|---|
| `/healthz` | GET | Health check |
| `/whoop/login` | GET | Start OAuth flow |
| `/whoop/callback` | GET | OAuth redirect handler |
| `/whoop/webhook` | POST | Receives WHOOP webhooks |
| `/debug/latest` | GET | Most recent stored data |

## How it works

1. You approve the app via browser -> tokens stored (encrypted)
2. WHOOP sends webhooks -> events recorded, hydration kicks off as background task
3. Full WHOOP objects fetched and stored as raw JSON in Postgres
4. OpenClaw reads from Postgres

## OpenClaw access

From inside Docker (join the `whoop-net` network):
```
postgresql://whoop:whoop_dev@whoop-postgres:5432/whoop
```

Add to OpenClaw's docker-compose:
```yaml
networks:
  whoop-net:
    external: true
```

## Catch-up script

If any webhook events failed to hydrate, run:
```bash
python -m worker.hydrate
```

## Tables

- `tokens` — encrypted OAuth tokens
- `webhook_events` — webhook event log + hydration status
- `raw_sleep` / `raw_workout` / `raw_recovery` — full WHOOP objects as JSON
