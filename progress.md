# Progress

## Status: Fully operational. Ready for analysis layer.

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
- [x] `api/whoop_client.py` has full function library: `get_sleep`, `get_workouts`, `get_recovery`, `get_cycles`, `get_profile`, `get_body`, `get_recovery_for_cycle`, `get_sleep_for_cycle`, `list_records`
- [x] CLAUDE.md updated with full v2 API reference and all gotchas

## Bugs fixed

- OAuth: added `state` param (WHOOP requires 8+ chars)
- OAuth: callback URI built from `X-Forwarded-*` headers (ngrok proxy mismatch)
- OAuth: `user_id` not in token response — fetch from `/v2/user/profile/basic`
- API: all endpoints are v2 (`/developer/v2/`), not v1. Cycle was only one that happened to work on v1.

## Next

- [ ] OpenClaw analysis modules (design phase)
