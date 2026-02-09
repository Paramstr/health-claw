# OpenClaw Task: Build WHOOP Analysis System

## Context

You have a working WHOOP data ingestion pipeline at `/root/projects/whoop-ingest/`. Read `CLAUDE.md` for full project context. Read `progress.md` for current status.

The pipeline is live:
- Postgres running with WHOOP data (user `32850214`, Param Singh)
- `pull.py` CLI fetches fresh data from WHOOP API
- `api/whoop_client.py` has all the functions you need to call the WHOOP API
- Webhooks are configured and will fire on new sleep/workout/recovery events

Your job: **build an analysis system that gives Param genuinely useful, personalised health insights — not surface-level stats.**

## What to build

### 1. Analysis modules (`analysis/`)

Create an `analysis/` directory inside this project. Each module is a self-contained Python file that:
- Reads from Postgres and/or calls `api/whoop_client.py` functions
- Computes insights against Param's **personal baselines** (not generic thresholds)
- Returns structured results that the reporter can format

#### Required modules:

**`analysis/baselines.py`** — Personal baseline calculator
- Compute rolling 7-day and 30-day averages for: HRV, RHR, sleep duration, strain, recovery score, SpO2
- All other modules compare against these baselines, not hardcoded numbers
- Must update baselines as new data arrives

**`analysis/sleep.py`** — Sleep analysis
- Sleep performance trends (not just last night — is it getting better or worse?)
- Sleep stage distribution (REM%, deep%, light%) compared to personal norm
- Sleep consistency: is bedtime/wake time regular? Does consistency correlate with recovery?
- Sleep debt: are short nights accumulating? When does payoff happen?
- Detect: unusually low REM or deep sleep relative to personal baseline

**`analysis/recovery.py`** — Recovery analysis
- Recovery trend (improving/declining over past 7-14 days)
- Recovery prediction: given last night's sleep, what recovery should we expect?
- Recovery vs sleep duration correlation (how strong is it for Param specifically?)
- Detect: recovery diverging from what sleep would predict (could indicate illness, stress, overtraining)

**`analysis/strain.py`** — Training load analysis
- Strain trend and load accumulation (acute vs chronic — is training load sustainable?)
- Strain-to-recovery ratio: is Param pushing harder than recovery allows?
- Detect: strain creeping up while recovery is flat or declining (overtraining signal)
- Workout intensity distribution: are workouts all the same or varied?

**`analysis/anomalies.py`** — Anomaly detection
- Flag any metric that deviates >1.5 standard deviations from personal 30-day baseline
- SpO2 drops (significant because Param's recently dipped from 96% to 91%)
- RHR trending up over 3+ days (early illness/overtraining warning)
- HRV suppression despite adequate sleep
- Provide severity levels: info, watch, alert

**`analysis/correlations.py`** — Pattern mining
- Day-of-week patterns (does sleep quality or recovery vary by day?)
- Post-high-strain recovery patterns (how long to bounce back?)
- Sleep duration → next-day recovery (quantify the relationship for Param)
- Find any non-obvious correlations in the data

**`analysis/investigations.py`** — Investigation trees
- When an anomaly is detected, trace the cause chain
- Example: low recovery → check sleep → check prior day strain → build explanation
- Produces natural language explanations, not just numbers
- This is what makes insights non-superficial

### 2. Reporter (`analysis/reporter.py`)

Sends insights to the **"Param Health"** Telegram group. You already have access to the Telegram bot — use the existing configuration.

#### Report types:

**Morning brief** (daily, after sleep data arrives):
- Last night's sleep summary vs personal norm
- Recovery score + what it means in context
- Readiness assessment for the day
- Any anomalies or alerts

**Post-workout** (triggered by workout webhook):
- Strain context: how this session compares to norm
- Projected recovery impact
- Cumulative load check

**Weekly digest** (Sunday evening):
- Week-over-week trends for all key metrics
- Best/worst days and why
- Patterns detected
- Outliers and anomalies from the week
- Actionable takeaways (not generic advice — specific to Param's data)

**Alerts** (immediate, when anomalies.py flags something):
- What was detected
- Investigation tree output (the "why" chain)
- Severity level
- What to watch for

### 3. Scheduler (`analysis/scheduler.py`)

Runs the analysis pipeline:
- On new webhook events (sleep/workout/recovery updated)
- On a cron schedule (morning brief, weekly digest)
- Integrate with the existing FastAPI app or run as a separate process — your choice, but keep it simple

### 4. Data layer

You may need to create additional Postgres tables for:
- Computed baselines (so they don't need recalculating every time)
- Analysis results / insight history
- Alert state (to avoid repeating the same alert)

Add these to `api/models.py` and they'll auto-create on startup.

## How to work

1. **Read `CLAUDE.md` and `progress.md` first.** Understand what exists.
2. **Pull recent data** with `python pull.py all --json` to understand the data shapes.
3. **Build one module at a time.** For each module:
   - Write the code
   - Write tests in `tests/` directory
   - Run tests, ensure they pass
   - Commit with a clear message
   - Push to GitHub
   - Update `progress.md`
   - Post a status update to the Telegram group
4. **Build in this order:** baselines → sleep → recovery → strain → anomalies → correlations → investigations → reporter → scheduler
5. **After all modules are built:** wire up the scheduler, run end-to-end, verify Telegram messages arrive.

## Constraints

- **Stay inside this project.** All code goes in `/root/projects/whoop-ingest/`.
- **Use the venv.** Always `.venv/bin/pip` and `.venv/bin/python`. Never install into system Python.
- **Push to GitHub as you go.** Every module gets committed and pushed after tests pass.
- **Update `progress.md` as you go.** It should always reflect current state.
- **Post updates to Telegram.** After each module is built and tested, post a summary to "Param Health".
- **Use existing code.** `api/whoop_client.py` already has all the WHOOP API functions. `api/db.py` has the database session. Don't duplicate.
- **Test everything.** Create `tests/` directory. Each analysis module gets tests. Use pytest. Add `pytest` to requirements.txt.
- **Keep it real.** Use actual statistical methods where appropriate (rolling means, z-scores, Pearson correlation). Don't fake insights with hardcoded thresholds.
- **Be mindful of resources.** This is a 4-core, 12GB RAM VPS. Don't pull 6 months of data into memory for every analysis run.

## Success criteria

This is done when:
1. All analysis modules exist with passing tests
2. Morning brief arrives in Telegram after sleep data updates
3. Weekly digest runs and posts meaningful trends
4. Anomalies are detected and explained with investigation trees
5. Insights are personalised (based on Param's baselines, not generic)
6. Everything is committed to GitHub
7. `progress.md` is up to date

## What NOT to do

- Don't add a UI
- Don't refactor the ingestion pipeline
- Don't change the OAuth or webhook handling
- Don't add generic health advice ("drink more water")
- Don't report raw numbers without context ("your HRV was 68ms" means nothing — "your HRV is 8% above your 30-day average" means something)
