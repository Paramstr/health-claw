# whoop-ingest

WHOOP data ingestion pipeline. Receives live WHOOP data via OAuth + webhooks and stores raw JSON in Postgres.

## What this is

A dev-only pipeline that:
1. Handles WHOOP OAuth (user approves app in browser)
2. Receives WHOOP webhooks when sleep/workout/recovery data updates
3. Fetches the full WHOOP object via API
4. Stores raw JSON in Postgres
5. Provides `pull.py` CLI to pull data on demand

OpenClaw connects to the same Postgres to build analysis on top. This project does **not** do analysis — it only ingests.

## Architecture

```
WHOOP API  --->  FastAPI app (host, port 8000)  --->  Postgres (Docker, port 5432)
                  - OAuth flow                         - whoop-net network
                  - webhook receiver                   - OpenClaw reads from here
                  - background hydration
```

- FastAPI runs on host with venv (not in Docker)
- Postgres runs in Docker on `whoop-net` network
- ngrok exposes port 8000 for WHOOP OAuth redirects and webhooks
- Current ngrok URL: `https://unhanged-robbi-unconvertibly.ngrok-free.dev` (changes on restart)

## Key files

- `api/config.py` — loads `.env`, all config vars. Imported first by everything.
- `api/main.py` — FastAPI app. Routes + inline hydration via BackgroundTasks.
- `api/oauth.py` — WHOOP OAuth flow, token storage, token refresh, Fernet encryption. Fetches user_id from profile endpoint (not in token response).
- `api/webhook.py` — HMAC-SHA256 signature verification for WHOOP webhooks.
- `api/whoop_client.py` — All WHOOP API calls: single object fetch, list endpoints, cycle sub-resources, profile, body.
- `api/db.py` — Async SQLAlchemy engine and session factory.
- `api/models.py` — All DB models (Token, WebhookEvent, Raw{Sleep,Workout,Recovery}).
- `pull.py` — CLI to pull recent data. See usage below.
- `worker/hydrate.py` — Catch-up script for missed webhook events.

## pull.py usage

```bash
python pull.py                    # all data types, 5 records each
python pull.py sleep              # just sleep
python pull.py workout --limit 10 # 10 most recent workouts
python pull.py cycle recovery     # multiple types
python pull.py all --json         # raw JSON output
```

Valid types: `profile`, `body`, `cycle`, `sleep`, `workout`, `recovery`, `all`

## Running

```bash
docker compose up -d                                          # postgres
source .venv/bin/activate                                     # venv
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload     # app
ngrok http 8000                                               # separate terminal
```

## User

- User ID: `32850214` (Param Singh)
- OAuth connected and tokens stored

## Database

Postgres container: `whoop-postgres` on `whoop-net` network.

Connection from host: `postgresql+asyncpg://whoop:whoop_dev@localhost:5432/whoop`
Connection from Docker: `postgresql://whoop:whoop_dev@whoop-postgres:5432/whoop`

Tables are auto-created on app startup via `Base.metadata.create_all`. No alembic yet.

### Tables

| Table | Purpose | Key columns |
|---|---|---|
| `tokens` | Encrypted OAuth tokens per user | `user_id`, `access_token_enc`, `refresh_token_enc`, `expires_at` |
| `webhook_events` | Every webhook received, deduped by trace_id | `trace_id`, `event_type`, `whoop_id`, `hydrated` |
| `raw_sleep` | Full sleep JSON from WHOOP API | `id`, `user_id`, `payload_json` |
| `raw_workout` | Full workout JSON from WHOOP API | `id`, `user_id`, `payload_json` |
| `raw_recovery` | Full recovery JSON from WHOOP API | `id`, `user_id`, `payload_json` |

## WHOOP API (all v2)

- Auth URL: `https://api.prod.whoop.com/oauth/oauth2/auth`
- Token URL: `https://api.prod.whoop.com/oauth/oauth2/token`
- API base: `https://api.prod.whoop.com/developer/v2`
- Webhooks: v2 format

### Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/v2/user/profile/basic` | User profile (name, email) |
| GET | `/v2/user/measurement/body` | Body measurements (height, weight, max HR) |
| GET | `/v2/cycle` | List cycles (paginated) |
| GET | `/v2/cycle/{cycleId}` | Single cycle |
| GET | `/v2/cycle/{cycleId}/sleep` | Sleep for a cycle |
| GET | `/v2/cycle/{cycleId}/recovery` | Recovery for a cycle |
| GET | `/v2/activity/sleep` | List sleeps (paginated) |
| GET | `/v2/activity/sleep/{sleepId}` | Single sleep (UUID) |
| GET | `/v2/activity/workout` | List workouts (paginated) |
| GET | `/v2/activity/workout/{workoutId}` | Single workout (UUID) |
| GET | `/v2/recovery` | List recoveries (paginated) |

List endpoints accept: `limit` (max 25), `start`, `end` (ISO datetime), `nextToken`.

### Webhook events

- `sleep.updated`
- `workout.updated`
- `recovery.updated`

Payload: `{ type, id, user_id, trace_id }`

Note: v2 webhooks use UUIDs for sleep/workout IDs, integer IDs for cycles.

## Conventions

- Raw JSON only. No transforms, no derived columns, no analysis.
- Tokens encrypted at rest with Fernet (`APP_SECRET` env var).
- OAuth callback URI built from `X-Forwarded-*` headers (required for ngrok proxy).
- OAuth `state` parameter required by WHOOP (min 8 chars).
- `user_id` not in token response — fetched from `/v2/user/profile/basic` after token exchange.
- Webhook deduplication via `trace_id` unique constraint + `ON CONFLICT DO NOTHING`.
- Hydration happens inline as a FastAPI BackgroundTask (not a separate worker process).
- If hydration fails, the event stays `hydrated=False`. Run `python -m worker.hydrate` to retry.

## What NOT to add here

- No UI / React
- No analysis or dashboards
- No OpenClaw integration logic (OpenClaw reads Postgres directly)
- No production hardening
