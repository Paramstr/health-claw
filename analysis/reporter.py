"""
analysis/reporter.py — Formats analysis results into Telegram messages.

Sends to "Param Health" Telegram group using existing Telegram config.
Saves sent messages in InsightHistory table.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import InsightHistory
from analysis.sleep import SleepReport
from analysis.recovery import RecoveryReport
from analysis.strain import StrainReport
from analysis.anomalies import Anomaly
from analysis.investigations import Investigation
from analysis.correlations import CorrelationsReport

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


async def send_telegram(text: str) -> bool:
    """Send a message to the configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            if resp.status_code == 200:
                logger.info("Telegram message sent (%d chars)", len(text))
                return True
            else:
                logger.error("Telegram send failed: %s %s", resp.status_code, resp.text)
                return False
    except Exception:
        logger.exception("Telegram send error")
        return False


async def save_insight(
    session: Optional[AsyncSession],
    event_type: str,
    results_json: dict,
    message_text: str,
    alert_fingerprints: Optional[list[str]] = None,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
    user_id: str = "32850214",
):
    """Save an insight to the history table."""
    if not session:
        return
    session.add(InsightHistory(
        user_id=user_id,
        event_type=event_type,
        results_json=json.dumps(results_json, default=str),
        message_text=message_text,
        alert_fingerprints=json.dumps(alert_fingerprints) if alert_fingerprints else None,
        input_window_start=window_start,
        input_window_end=window_end,
    ))
    await session.commit()


def format_morning_brief(
    sleep: SleepReport,
    recovery: RecoveryReport,
    anomalies: list[Anomaly],
    investigations: list[Investigation],
) -> str:
    """Format the morning brief message."""
    lines = ["☀️ <b>Morning Brief</b>"]
    lines.append("")

    # Sleep summary
    lines.append("💤 <b>Sleep</b>")
    if sleep.last_sleep_duration_hrs is not None:
        dur = sleep.last_sleep_duration_hrs
        lines.append(f"  Duration: {dur:.1f}h", )
        if sleep.duration_vs_baseline_pct is not None:
            lines[-1] += f" ({sleep.duration_vs_baseline_pct:+.0f}% vs baseline)"
    if sleep.efficiency_pct is not None:
        lines.append(f"  Efficiency: {sleep.efficiency_pct:.0f}%")
    if sleep.stages:
        s = sleep.stages
        lines.append(f"  Stages: REM {s.current_rem_pct:.0f}% | Deep {s.current_deep_pct:.0f}% | Light {s.current_light_pct:.0f}%")
        if s.rem_flag or s.deep_flag:
            flags = []
            if s.rem_flag:
                flags.append(f"REM {s.rem_flag}")
            if s.deep_flag:
                flags.append(f"Deep {s.deep_flag}")
            lines.append(f"  ⚠️ {', '.join(flags)} vs your norm")
    if sleep.debt and sleep.debt.current_debt_hrs > 2:
        lines.append(f"  Sleep debt: {sleep.debt.current_debt_hrs:.1f}h ({sleep.debt.trend})")
    if sleep.trend and sleep.trend.direction != "stable":
        lines.append(f"  Trend: {sleep.trend.direction} ({sleep.trend.duration_slope_hrs_per_day:+.2f}h/day)")

    lines.append("")

    # Recovery summary
    lines.append("💚 <b>Recovery</b>")
    if recovery.latest_score is not None:
        emoji = "🟢" if recovery.latest_score >= 67 else "🟡" if recovery.latest_score >= 34 else "🔴"
        lines.append(f"  {emoji} Score: {recovery.latest_score:.0f}%")
        if recovery.score_vs_baseline_pct is not None:
            lines[-1] += f" ({recovery.score_vs_baseline_pct:+.0f}% vs baseline)"
    if recovery.trend and recovery.trend.direction != "stable":
        lines.append(f"  Trend: {recovery.trend.direction} over last 7d (avg {recovery.trend.avg_7d:.0f}%)")
    if recovery.prediction:
        p = recovery.prediction
        if p.divergence_flag:
            lines.append(f"  ⚠️ Expected {p.predicted_score:.0f}% from sleep, got {p.actual_score:.0f}%")

    # Readiness
    lines.append("")
    if recovery.latest_score is not None:
        if recovery.latest_score >= 67:
            lines.append("✅ <b>Ready for high intensity today</b>")
        elif recovery.latest_score >= 50:
            lines.append("⚡ <b>Moderate intensity recommended</b>")
        elif recovery.latest_score >= 34:
            lines.append("🧘 <b>Light activity or active recovery</b>")
        else:
            lines.append("🛏️ <b>Rest day recommended</b>")

    # Anomalies & investigations
    alerts = [a for a in anomalies if a.severity in ("watch", "alert")]
    if alerts:
        lines.append("")
        lines.append("🚨 <b>Alerts</b>")
        for a in alerts:
            icon = "🔴" if a.severity == "alert" else "🟡"
            lines.append(f"  {icon} {a.description}")

        for inv in investigations:
            if inv.anomaly in alerts and inv.cause_chain:
                lines.append(f"  → {inv.summary}")

    return "\n".join(lines)


def format_post_workout(
    strain: StrainReport,
    recovery: RecoveryReport,
) -> str:
    """Format the post-workout message."""
    lines = ["🏋️ <b>Post-Workout</b>"]
    lines.append("")

    if strain.latest_day_strain is not None:
        lines.append(f"  Day strain: {strain.latest_day_strain:.1f}")
        if strain.strain_vs_baseline_pct is not None:
            lines[-1] += f" ({strain.strain_vs_baseline_pct:+.0f}% vs baseline)"

    if strain.acute_chronic:
        ac = strain.acute_chronic
        lines.append(f"  Acute:Chronic ratio: {ac.ratio:.2f} ({ac.risk_level} risk)")

    if strain.balance:
        b = strain.balance
        lines.append(f"  Strain-recovery balance: {b.ratio_trend}")
        if b.drift_direction:
            lines.append(f"  ⚠️ Gap {b.drift_direction}")

    if strain.overtraining and strain.overtraining.detected:
        ot = strain.overtraining
        lines.append(f"  ⚠️ Overtraining signal ({ot.confidence})")

    # Projected recovery impact
    lines.append("")
    if recovery.latest_score is not None and strain.latest_day_strain is not None:
        if strain.latest_day_strain > 16:
            lines.append("📉 High strain — expect lower recovery tomorrow")
        elif strain.latest_day_strain > 12:
            lines.append("📊 Moderate strain — recovery should hold steady")
        else:
            lines.append("📈 Light day — recovery likely to improve")

    if strain.flags:
        lines.append("")
        for f in strain.flags:
            lines.append(f"  ⚠️ {f}")

    return "\n".join(lines)


def format_weekly_digest(
    sleep: SleepReport,
    recovery: RecoveryReport,
    strain: StrainReport,
    correlations: CorrelationsReport,
    anomalies: list[Anomaly],
) -> str:
    """Format the weekly digest message."""
    lines = ["📊 <b>Weekly Digest</b>"]
    lines.append("")

    # Week trends
    lines.append("<b>Trends This Week</b>")
    if recovery.trend:
        lines.append(f"  Recovery: {recovery.trend.direction} (7d avg {recovery.trend.avg_7d:.0f}%)")
    if sleep.trend:
        lines.append(f"  Sleep: {sleep.trend.direction} (7d avg {sleep.trend.duration_7d_avg_hrs:.1f}h)")
    if strain.acute_chronic:
        lines.append(f"  Load: {strain.acute_chronic.acute_7d:.1f} avg daily strain (ACWR {strain.acute_chronic.ratio:.2f})")

    # Workout summary
    if strain.workout_dist:
        d = strain.workout_dist
        lines.append("")
        lines.append("<b>Workouts</b>")
        lines.append(f"  {d.total_workouts} sessions | Avg strain {d.avg_strain_per_workout:.1f}")
        lines.append(f"  High {d.high_intensity_count} | Moderate {d.moderate_count} | Low {d.low_count}")

    # Consistency
    if sleep.consistency:
        lines.append("")
        lines.append("<b>Consistency</b>")
        lines.append(f"  Sleep timing score: {sleep.consistency.consistency_score:.0f}/100")
        lines.append(f"  Bedtime variance: ±{sleep.consistency.bedtime_std_minutes:.0f}min")

    # Patterns
    if correlations.sleep_to_recovery:
        s2r = correlations.sleep_to_recovery
        lines.append("")
        lines.append("<b>Patterns</b>")
        lines.append(f"  {s2r.interpretation}")

    if correlations.bounce_back:
        bb = correlations.bounce_back
        lines.append(f"  Recovery bounce-back: ~{bb.avg_days_to_recover:.1f} days after high strain")

    # Alerts summary
    alert_count = sum(1 for a in anomalies if a.severity in ("watch", "alert"))
    if alert_count:
        lines.append("")
        lines.append(f"⚠️ {alert_count} alert(s) this week — check daily briefs for details")

    return "\n".join(lines)


def format_alert(anomaly: Anomaly, investigation: Optional[Investigation] = None) -> str:
    """Format an immediate alert message."""
    icon = "🔴" if anomaly.severity == "alert" else "🟡" if anomaly.severity == "watch" else "ℹ️"
    lines = [f"{icon} <b>Health Alert — {anomaly.metric.upper()}</b>"]
    lines.append("")
    lines.append(anomaly.description)

    if investigation and investigation.cause_chain:
        lines.append("")
        lines.append("<b>Investigation:</b>")
        for c in investigation.cause_chain:
            contrib_icon = "→" if c.contribution == "likely" else "·"
            lines.append(f"  {contrib_icon} {c.factor}: {c.evidence}")
        lines.append("")
        lines.append(investigation.summary)

    return "\n".join(lines)
