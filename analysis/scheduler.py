"""
analysis/scheduler.py — Orchestrates analysis runs.

Entry points:
- handle_sleep_event(payload) — triggered by sleep webhook
- handle_workout_event(payload) — triggered by workout webhook
- handle_recovery_event(payload) — triggered by recovery webhook
- run_morning_brief(date) — manual or cron trigger
- run_weekly_digest(week_end_date) — manual or cron trigger

Idempotency:
- Morning brief: one per date
- Alerts: cooldown window per fingerprint
- Weekly digest: one per week-ending date
"""

import json
import logging
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import InsightHistory, AlertState
from api.db import async_session

from analysis.baselines import compute_baselines, get_all_baselines
from analysis.sleep import analyze_sleep
from analysis.recovery import analyze_recovery
from analysis.strain import analyze_strain
from analysis.anomalies import detect_all_anomalies, Anomaly
from analysis.correlations import analyze_correlations
from analysis.investigations import investigate_anomalies
from analysis.reporter import (
    format_morning_brief,
    format_post_workout,
    format_weekly_digest,
    format_alert,
    send_telegram,
    save_insight,
)

logger = logging.getLogger(__name__)

ALERT_COOLDOWN_HOURS = 12


async def _already_sent(session: AsyncSession, event_type: str, date_key: str) -> bool:
    """Check if we already sent this type of report for the given date."""
    stmt = select(InsightHistory).where(
        and_(
            InsightHistory.event_type == event_type,
            InsightHistory.results_json.contains(date_key),
        )
    ).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _alert_on_cooldown(session: AsyncSession, fingerprint: str, user_id: str = "32850214") -> bool:
    """Check if an alert fingerprint is within cooldown."""
    stmt = select(AlertState).where(
        and_(
            AlertState.user_id == user_id,
            AlertState.fingerprint == fingerprint,
            AlertState.resolved == False,
        )
    ).limit(1)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if not row:
        return False
    if row.last_sent:
        elapsed = datetime.now(timezone.utc) - row.last_sent.replace(tzinfo=timezone.utc)
        return elapsed.total_seconds() < ALERT_COOLDOWN_HOURS * 3600
    return False


async def _record_alert(session: AsyncSession, anomaly: Anomaly, user_id: str = "32850214"):
    """Record or update alert state."""
    stmt = select(AlertState).where(
        and_(
            AlertState.user_id == user_id,
            AlertState.fingerprint == anomaly.fingerprint,
        )
    ).limit(1)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row:
        row.last_sent = datetime.now(timezone.utc)
        row.send_count = (row.send_count or 0) + 1
        row.severity = anomaly.severity
        row.detail_json = json.dumps({"description": anomaly.description}, default=str)
    else:
        session.add(AlertState(
            user_id=user_id,
            fingerprint=anomaly.fingerprint,
            severity=anomaly.severity,
            send_count=1,
            detail_json=json.dumps({"description": anomaly.description}, default=str),
        ))
    await session.commit()


async def handle_sleep_event(event_payload: dict):
    """
    Called when a sleep webhook arrives. Triggers morning brief.
    """
    logger.info("Sleep event received: %s", event_payload.get("id", "unknown"))
    async with async_session() as session:
        today = date.today().isoformat()

        # Idempotency: one morning brief per day
        if await _already_sent(session, "morning_brief", today):
            logger.info("Morning brief already sent for %s, skipping", today)
            return

        await run_morning_brief(today, session)


async def handle_workout_event(event_payload: dict):
    """Called when a workout webhook arrives. Triggers post-workout report."""
    logger.info("Workout event received: %s", event_payload.get("id", "unknown"))
    async with async_session() as session:
        await run_post_workout(session)


async def handle_recovery_event(event_payload: dict):
    """Called when a recovery webhook arrives. Updates baselines and checks anomalies."""
    logger.info("Recovery event received: %s", event_payload.get("id", "unknown"))
    async with async_session() as session:
        # Refresh baselines
        await compute_baselines(session)

        # Check for anomalies
        baselines = await get_all_baselines(session)
        anomalies = await detect_all_anomalies(session)
        investigations = await investigate_anomalies(anomalies, baselines)

        # Send alerts for severe anomalies
        for anomaly in anomalies:
            if anomaly.severity not in ("watch", "alert"):
                continue
            if await _alert_on_cooldown(session, anomaly.fingerprint):
                logger.info("Alert %s on cooldown, skipping", anomaly.fingerprint)
                continue

            inv = next((i for i in investigations if i.anomaly == anomaly), None)
            msg = format_alert(anomaly, inv)
            sent = await send_telegram(msg)
            if sent:
                await _record_alert(session, anomaly)
                await save_insight(
                    session, "alert",
                    {"anomaly": anomaly.description, "severity": anomaly.severity},
                    msg,
                    alert_fingerprints=[anomaly.fingerprint],
                )


async def run_morning_brief(date_str: str = None, session: AsyncSession = None):
    """Run the morning brief analysis and send to Telegram."""
    if date_str is None:
        date_str = date.today().isoformat()

    own_session = session is None
    if own_session:
        ctx = async_session()
        session = await ctx.__aenter__()

    try:
        # Update baselines first
        await compute_baselines(session)
        baselines = await get_all_baselines(session)

        # Run analyses
        sleep_report = await analyze_sleep(session)
        recovery_report = await analyze_recovery(session)
        anomalies = await detect_all_anomalies(session)
        investigations = await investigate_anomalies(anomalies, baselines)

        # Format and send
        msg = format_morning_brief(sleep_report, recovery_report, anomalies, investigations)
        sent = await send_telegram(msg)

        # Store insight
        await save_insight(
            session, "morning_brief",
            {
                "date": date_str,
                "sleep_duration": sleep_report.last_sleep_duration_hrs,
                "recovery_score": recovery_report.latest_score,
                "anomaly_count": len(anomalies),
            },
            msg,
        )

        logger.info("Morning brief for %s: %s", date_str, "sent" if sent else "stored (telegram not configured)")
    finally:
        if own_session:
            await ctx.__aexit__(None, None, None)


async def run_post_workout(session: AsyncSession = None):
    """Run post-workout analysis and send."""
    own_session = session is None
    if own_session:
        ctx = async_session()
        session = await ctx.__aenter__()

    try:
        strain_report = await analyze_strain(session)
        recovery_report = await analyze_recovery(session)

        msg = format_post_workout(strain_report, recovery_report)
        sent = await send_telegram(msg)

        await save_insight(
            session, "post_workout",
            {
                "strain": strain_report.latest_day_strain,
                "acwr": strain_report.acute_chronic.ratio if strain_report.acute_chronic else None,
            },
            msg,
        )

        logger.info("Post-workout report: %s", "sent" if sent else "stored")
    finally:
        if own_session:
            await ctx.__aexit__(None, None, None)


async def run_weekly_digest(week_end_date: str = None, session: AsyncSession = None):
    """Run weekly digest and send."""
    if week_end_date is None:
        week_end_date = date.today().isoformat()

    own_session = session is None
    if own_session:
        ctx = async_session()
        session = await ctx.__aenter__()

    try:
        # Idempotency
        if await _already_sent(session, "weekly_digest", week_end_date):
            logger.info("Weekly digest already sent for %s", week_end_date)
            return

        await compute_baselines(session)
        baselines = await get_all_baselines(session)

        sleep_report = await analyze_sleep(session)
        recovery_report = await analyze_recovery(session)
        strain_report = await analyze_strain(session)
        correlations = await analyze_correlations()
        anomalies = await detect_all_anomalies(session)

        msg = format_weekly_digest(sleep_report, recovery_report, strain_report, correlations, anomalies)
        sent = await send_telegram(msg)

        await save_insight(
            session, "weekly_digest",
            {
                "week_end": week_end_date,
                "recovery_7d_avg": recovery_report.trend.avg_7d if recovery_report.trend else None,
                "sleep_7d_avg": sleep_report.trend.duration_7d_avg_hrs if sleep_report.trend else None,
            },
            msg,
        )

        logger.info("Weekly digest for %s: %s", week_end_date, "sent" if sent else "stored")
    finally:
        if own_session:
            await ctx.__aexit__(None, None, None)
