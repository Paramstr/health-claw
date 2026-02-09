# Progress

## Status: Building analysis system (Module 1/9 done)

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

## Analysis System

### DB Schema
- [x] Added Baseline, InsightHistory, AlertState models to api/models.py

### Modules
- [x] 1. `analysis/baselines.py` — Rolling 7d/30d baselines (HRV, RHR, sleep duration, strain, recovery, SpO2). 19 tests passing.
- [ ] 2. `analysis/sleep.py`
- [ ] 3. `analysis/recovery.py`
- [ ] 4. `analysis/strain.py`
- [ ] 5. `analysis/anomalies.py`
- [ ] 6. `analysis/correlations.py`
- [ ] 7. `analysis/investigations.py`
- [ ] 8. `analysis/reporter.py`
- [ ] 9. `analysis/scheduler.py` + webhook wiring

### How to run tests
```bash
cd /root/projects/whoop-ingest
.venv/bin/python -m pytest tests/ -v
```

## Bugs fixed

- OAuth: added `state` param (WHOOP requires 8+ chars)
- OAuth: callback URI built from `X-Forwarded-*` headers (ngrok proxy mismatch)
- OAuth: `user_id` not in token response — fetch from `/v2/user/profile/basic`
- API: all endpoints are v2 (`/developer/v2/`), not v1.
