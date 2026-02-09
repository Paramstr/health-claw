# Progress

## Status: Analysis system complete ✅ — 127 tests passing

## Done

- [x] Project created at `/root/projects/whoop-ingest/`
- [x] venv + all deps installed
- [x] `.env` configured (WHOOP creds, Fernet key, Postgres)
- [x] Postgres running in Docker on `whoop-net` network
- [x] FastAPI running on host, port 8000, `--reload`
- [x] ngrok installed, running, tunneling to port 8000
- [x] WHOOP developer portal configured (redirect URI, webhook URL, v2)
- [x] OAuth flow working — user 32850214 (Param Singh) connected
- [x] Tokens stored encrypted in Postgres
- [x] All WHOOP API endpoints confirmed working (all v2, not v1)
- [x] `pull.py` CLI created for on-demand data pulls
- [x] CLAUDE.md updated with full v2 API reference and all gotchas

## Analysis System ✅

### DB Schema
- [x] Baseline table (rolling 7d/30d per metric)
- [x] InsightHistory table (structured JSON per report/event)
- [x] AlertState table (dedupe alerts, fingerprint, cooldown)

### Modules (all complete with tests)
1. [x] `analysis/baselines.py` — Rolling 7d/30d baselines (HRV, RHR, sleep duration, strain, recovery, SpO2). 19 tests.
2. [x] `analysis/sleep.py` — Trend, stage distribution, consistency, sleep debt, z-score detection. 20 tests.
3. [x] `analysis/recovery.py` — Trend, sleep-based prediction (multivariate regression), correlation, divergence. 14 tests.
4. [x] `analysis/strain.py` — Acute:chronic ratio, strain-recovery balance, overtraining detection, workout distribution. 16 tests.
5. [x] `analysis/anomalies.py` — Z-score anomalies, SpO2 drops, RHR trends, HRV suppression, severity levels, fingerprinting. 22 tests.
6. [x] `analysis/correlations.py` — Day-of-week patterns, bounce-back time, sleep→recovery quantification, correlation search. 10 tests.
7. [x] `analysis/investigations.py` — Cause chain builder for low recovery, high strain, SpO2 drops. 8 tests.
8. [x] `analysis/reporter.py` — Telegram formatting (morning brief, post-workout, weekly digest, alerts), insight storage. 11 tests.
9. [x] `analysis/scheduler.py` — Orchestrator with idempotency, cooldowns, webhook handlers. 7 tests.

### Webhook Wiring
- [x] `api/main.py` updated: hydrate() now triggers analysis handlers after successful hydration
- [x] Lazy import to avoid circular deps

### Report Types
- [x] Morning brief (after sleep data)
- [x] Post-workout (after workout data)
- [x] Weekly digest (manual/cron trigger)
- [x] Alerts (immediate, with investigation chains)

## Remaining Setup (manual)
- [ ] Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to `.env`
- [ ] Create new DB tables: restart FastAPI (auto-creates via `Base.metadata.create_all`)
- [ ] Configure git push credentials for feature branch
- [ ] Optional: Add host cron for weekly digest (Sunday evening)

### How to run tests
```bash
cd /root/projects/whoop-ingest
.venv/bin/python -m pytest tests/ -v
```

### How to manually trigger reports
```bash
cd /root/projects/whoop-ingest
.venv/bin/python -c "
import asyncio
from analysis.scheduler import run_morning_brief, run_weekly_digest
asyncio.run(run_morning_brief())
# asyncio.run(run_weekly_digest())
"
```

## Bugs fixed

- OAuth: added `state` param (WHOOP requires 8+ chars)
- OAuth: callback URI built from `X-Forwarded-*` headers (ngrok proxy mismatch)
- OAuth: `user_id` not in token response — fetch from `/v2/user/profile/basic`
- API: all endpoints are v2 (`/developer/v2/`), not v1.
