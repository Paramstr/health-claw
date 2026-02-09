"""
analysis/scheduler.py — Orchestrator that ties all analysis modules together.

Runs:
- morning_brief: Triggered after sleep data arrives (or on schedule ~7-8am)
- post_workout: Triggered by webhook when workout completes
- weekly_digest: Sunday evening
- alert_check: Periodic anomaly scan

Idempotency: tracks last run per report type to avoid duplicates.
"""

import logging
from datetime import datetime, date, timedelta, timezone
from dataclasses import dataclass
from typing import Optional

from analysis.baselines import (
    get_all_baselines,
    compute_baselines,
    _fetch_recovery_data,
    _fetch_sleep_data,
    _fetch_cycle_data,
    _record_date,
    _extract_metric_from_recovery,
    _extract_sleep_duration,
    _extract_strain,
)
from analysis.sleep import analyze_sleep
from analysis.recovery import analyze_recovery
from analysis.strain import analyze_strain
from analysis.anomalies import detect_all_anomalies, Anomaly
from analysis.correlations import analyze_correlations
from analysis.investigations import investigate_all
from analysis.reporter import (
    format_morning_brief,
    format_post_workout,
    format_weekly_digest,
    format_alert,
)

logger = logging.getLogger(__name__)

# In-memory idempotency tracker (reset on restart — fine for single instance)
_last_runs: dict[str, str] = {}  # report_type -> "YYYY-MM-DD"


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _already_ran(report_type: str) -> bool:
    return _last_runs.get(report_type) == _today_str()


def _mark_ran(report_type: str):
    _last_runs[report_type] = _today_str()


def reset_idempotency():
    """Reset for testing."""
    _last_runs.clear()


async def run_morning_brief(session=None, force: bool = False) -> Optional[str]:
    """Generate morning brief. Returns formatted message or None if already sent today."""
    if not force and _already_ran("morning_brief"):
        logger.info("Morning brief already sent today, skipping")
        return None

    recovery_data = await _fetch_recovery_data(7)
    sleep_data = await _fetch_sleep_data(7)

    if not recovery_data or not sleep_data:
        logger.warning("No recovery or sleep data available for morning brief")
        return None

    latest_recovery = recovery_data[0]
    latest_sleep = sleep_data[0]

    recovery_score = _extract_metric_from_recovery(latest_recovery, "recovery")
    hrv = _extract_metric_from_recovery(latest_recovery, "hrv")
    rhr = _extract_metric_from_recovery(latest_recovery, "rhr")
    spo2 = _extract_metric_from_recovery(latest_recovery, "spo2")
    sleep_hours = _extract_sleep_duration(latest_sleep)

    # Sleep quality from analysis
    sleep_report = await analyze_sleep(session)
    sleep_quality = None
    if sleep_report and sleep_report.efficiency_pct is not None:
        eff = sleep_report.efficiency_pct
        if eff >= 85:
            sleep_quality = f"Good efficiency ({eff:.0f}%)"
        elif eff >= 70:
            sleep_quality = f"Fair efficiency ({eff:.0f}%)"
        else:
            sleep_quality = f"Poor efficiency ({eff:.0f}%)"

    # Anomalies
    anomalies = await detect_all_anomalies(session)
    investigations = await investigate_all(anomalies)

    msg = format_morning_brief(
        recovery_score, hrv, rhr, spo2, sleep_hours, sleep_quality,
        anomalies, investigations,
    )

    _mark_ran("morning_brief")
    return msg


async def run_post_workout(workout_data: dict, session=None) -> Optional[str]:
    """Generate post-workout report from webhook data."""
    score = workout_data.get("score", {})
    strain = score.get("strain")
    if strain is None:
        return None

    # Get strain baseline for z-score
    baselines = {}
    if session:
        baselines = await get_all_baselines(session)
    strain_bl = baselines.get("strain", {}).get("30d")
    strain_z = None
    if strain_bl and strain_bl.std > 0:
        strain_z = (strain - strain_bl.mean) / strain_bl.std

    activity_name = workout_data.get("sport_id")  # WHOOP uses sport_id
    duration_ms = score.get("duration_milli") or score.get("time_in_zones_milli")
    duration_min = duration_ms / 60000 if duration_ms else None
    avg_hr = score.get("average_heart_rate")
    max_hr = score.get("max_heart_rate")
    calories = score.get("kilojoule")
    if calories is not None:
        calories = calories * 0.239006  # kJ to kcal

    return format_post_workout(
        strain, strain_z, activity_name, duration_min, avg_hr, max_hr, calories,
    )


async def run_weekly_digest(session=None, force: bool = False) -> Optional[str]:
    """Generate weekly digest. Returns formatted message or None."""
    if not force and _already_ran("weekly_digest"):
        logger.info("Weekly digest already sent today, skipping")
        return None

    recovery_data = await _fetch_recovery_data(7)
    sleep_data = await _fetch_sleep_data(7)
    cycle_data = await _fetch_cycle_data(7)

    if not recovery_data:
        return None

    import numpy as np

    recoveries = [_extract_metric_from_recovery(r, "recovery") for r in recovery_data]
    recoveries = [r for r in recoveries if r is not None]
    avg_recovery = float(np.mean(recoveries)) if recoveries else None

    hrvs = [_extract_metric_from_recovery(r, "hrv") for r in recovery_data]
    hrvs = [h for h in hrvs if h is not None]
    avg_hrv = float(np.mean(hrvs)) if hrvs else None

    sleeps = [_extract_sleep_duration(s) for s in sleep_data if not s.get("nap", False)]
    sleeps = [s for s in sleeps if s is not None]
    avg_sleep = float(np.mean(sleeps)) if sleeps else None

    strains = [_extract_strain(c) for c in cycle_data]
    strains = [s for s in strains if s is not None]
    avg_strain = float(np.mean(strains)) if strains else None

    # Best/worst day by recovery
    best_day = worst_day = None
    if recoveries and recovery_data:
        day_scores = []
        for r in recovery_data:
            d = _record_date(r)
            score = _extract_metric_from_recovery(r, "recovery")
            if d and score is not None:
                day_scores.append((d.strftime("%A %m/%d"), score))
        if day_scores:
            best_day = max(day_scores, key=lambda x: x[1])[0]
            worst_day = min(day_scores, key=lambda x: x[1])[0]

    # Anomalies this week
    anomalies = await detect_all_anomalies(session)
    alert_count = sum(1 for a in anomalies if a.severity == "alert")

    # Correlations
    correlations = await analyze_correlations(session)

    msg = format_weekly_digest(
        avg_recovery, avg_hrv, avg_sleep, avg_strain,
        best_day, worst_day, correlations,
        len(anomalies), alert_count,
    )

    _mark_ran("weekly_digest")
    return msg


async def run_alert_check(session=None) -> list[str]:
    """Check for anomalies and return alert messages for any alert-severity items."""
    anomalies = await detect_all_anomalies(session)
    alerts = [a for a in anomalies if a.severity == "alert"]

    if not alerts:
        return []

    investigations = await investigate_all(alerts)
    inv_map = {inv.anomaly.fingerprint: inv for inv in investigations}

    messages = []
    for a in alerts:
        inv = inv_map.get(a.fingerprint)
        messages.append(format_alert(a, inv))

    return messages


async def handle_webhook(event_type: str, data: dict, session=None) -> Optional[str]:
    """Handle WHOOP webhook events. Returns message to send or None."""
    if event_type == "recovery.updated":
        return await run_morning_brief(session)
    elif event_type == "workout.updated":
        return await run_post_workout(data, session)
    elif event_type == "sleep.updated":
        # Sleep update can also trigger morning brief
        return await run_morning_brief(session)
    return None
