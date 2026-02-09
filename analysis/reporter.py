"""
analysis/reporter.py — Format analysis results for Telegram.

Report types:
- morning_brief: Post-sleep summary (recovery, sleep, anomalies)
- post_workout: After high strain (strain, recovery impact)
- weekly_digest: Sunday summary (trends, correlations, best/worst)
- alert: Immediate anomaly alerts

All formatting is Telegram HTML. No generic health advice — everything grounded in data.
"""

import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

from analysis.anomalies import Anomaly
from analysis.investigations import Investigation
from analysis.correlations import CorrelationsReport, SleepRecoveryQuantified

logger = logging.getLogger(__name__)

SEVERITY_EMOJI = {"alert": "🚨", "watch": "⚠️", "info": "ℹ️"}
METRIC_EMOJI = {
    "hrv": "💓", "rhr": "❤️", "recovery": "🔋", "spo2": "🫁",
    "sleep_duration": "😴", "strain": "🏋️",
}


@dataclass
class ReportSection:
    title: str
    body: str
    priority: int = 0  # higher = more important


def _fmt_anomaly(a: Anomaly) -> str:
    emoji = SEVERITY_EMOJI.get(a.severity, "")
    return f"{emoji} <b>{a.severity.upper()}</b>: {a.description}"


def _fmt_investigation(inv: Investigation) -> str:
    lines = [f"🔍 <b>Likely cause: {inv.hypothesis.replace('_', ' ').title()}</b> ({inv.confidence} confidence)"]
    for link in inv.cause_chain[:4]:
        prefix = f"  └ Day-{link.days_prior}" if link.days_prior > 0 else "  └ Today"
        lines.append(f"{prefix}: {link.observation}")
    lines.append(f"💡 {inv.recommendation}")
    return "\n".join(lines)


def format_morning_brief(
    recovery_score: Optional[float],
    hrv: Optional[float],
    rhr: Optional[float],
    spo2: Optional[float],
    sleep_hours: Optional[float],
    sleep_quality: Optional[str],
    anomalies: list[Anomaly],
    investigations: list[Investigation],
) -> str:
    """Format the morning brief report."""
    lines = ["☀️ <b>Morning Brief</b>", ""]

    # Recovery overview
    if recovery_score is not None:
        if recovery_score >= 67:
            emoji = "🟢"
        elif recovery_score >= 34:
            emoji = "🟡"
        else:
            emoji = "🔴"
        lines.append(f"{emoji} Recovery: <b>{recovery_score:.0f}%</b>")

    metrics = []
    if hrv is not None:
        metrics.append(f"💓 HRV: {hrv:.0f}ms")
    if rhr is not None:
        metrics.append(f"❤️ RHR: {rhr:.0f} bpm")
    if spo2 is not None:
        metrics.append(f"🫁 SpO2: {spo2:.1f}%")
    if metrics:
        lines.append(" · ".join(metrics))

    # Sleep
    if sleep_hours is not None:
        lines.append(f"\n😴 Sleep: <b>{sleep_hours:.1f}h</b>")
        if sleep_quality:
            lines.append(f"   {sleep_quality}")

    # Anomalies
    alerts = [a for a in anomalies if a.severity in ("alert", "watch")]
    if alerts:
        lines.append("")
        for a in alerts:
            lines.append(_fmt_anomaly(a))

    # Investigations
    if investigations:
        lines.append("")
        for inv in investigations:
            lines.append(_fmt_investigation(inv))

    if not alerts and not investigations:
        lines.append("\n✅ All metrics within normal range.")

    return "\n".join(lines)


def format_post_workout(
    strain: float,
    strain_z: Optional[float],
    activity_name: Optional[str],
    duration_min: Optional[float],
    avg_hr: Optional[float],
    max_hr: Optional[float],
    calories: Optional[float],
) -> str:
    """Format post-workout report."""
    lines = ["🏋️ <b>Post-Workout</b>", ""]

    if activity_name:
        lines.append(f"Activity: <b>{activity_name}</b>")

    lines.append(f"Strain: <b>{strain:.1f}</b>")
    if strain_z is not None:
        if abs(strain_z) > 2:
            lines.append(f"   {'⬆️ Well above' if strain_z > 0 else '⬇️ Well below'} your usual (z={strain_z:+.1f})")
        elif abs(strain_z) > 1:
            lines.append(f"   {'↗️ Above' if strain_z > 0 else '↘️ Below'} average (z={strain_z:+.1f})")

    details = []
    if duration_min is not None:
        details.append(f"⏱ {duration_min:.0f} min")
    if avg_hr is not None:
        details.append(f"❤️ Avg {avg_hr:.0f} bpm")
    if max_hr is not None:
        details.append(f"Max {max_hr:.0f} bpm")
    if calories is not None:
        details.append(f"🔥 {calories:.0f} cal")
    if details:
        lines.append(" · ".join(details))

    if strain > 16:
        lines.append("\n💡 High strain day — prioritize sleep and hydration tonight.")

    return "\n".join(lines)


def format_weekly_digest(
    avg_recovery: Optional[float],
    avg_hrv: Optional[float],
    avg_sleep: Optional[float],
    avg_strain: Optional[float],
    best_day: Optional[str],
    worst_day: Optional[str],
    correlations: Optional[CorrelationsReport],
    anomaly_count: int,
    alert_count: int,
) -> str:
    """Format Sunday weekly digest."""
    lines = ["📊 <b>Weekly Digest</b>", ""]

    # Averages
    lines.append("<b>This Week's Averages</b>")
    if avg_recovery is not None:
        lines.append(f"  🔋 Recovery: {avg_recovery:.0f}%")
    if avg_hrv is not None:
        lines.append(f"  💓 HRV: {avg_hrv:.0f}ms")
    if avg_sleep is not None:
        lines.append(f"  😴 Sleep: {avg_sleep:.1f}h/night")
    if avg_strain is not None:
        lines.append(f"  🏋️ Strain: {avg_strain:.1f}/day")

    # Best/worst
    if best_day or worst_day:
        lines.append("")
        if best_day:
            lines.append(f"🏆 Best day: {best_day}")
        if worst_day:
            lines.append(f"📉 Toughest day: {worst_day}")

    # Alerts summary
    if anomaly_count > 0:
        lines.append(f"\n⚡ {anomaly_count} anomalies detected ({alert_count} alerts)")

    # Correlations insights
    if correlations and correlations.sleep_to_recovery:
        s2r = correlations.sleep_to_recovery
        if abs(s2r.correlation) > 0.3:
            lines.append(f"\n📈 <b>Insight:</b> {s2r.interpretation}")

    if correlations and correlations.bounce_back:
        bb = correlations.bounce_back
        lines.append(f"🔄 After hard training days, you typically bounce back in ~{bb.avg_days_to_recover:.1f} days")

    if correlations and correlations.day_of_week:
        sig = [p for p in correlations.day_of_week if p.significant]
        for p in sig[:2]:
            lines.append(f"📅 {p.metric}: best on {p.best_day}, weakest on {p.worst_day}")

    return "\n".join(lines)


def format_alert(anomaly: Anomaly, investigation: Optional[Investigation] = None) -> str:
    """Format an immediate alert."""
    lines = [_fmt_anomaly(anomaly)]
    if investigation:
        lines.append("")
        lines.append(_fmt_investigation(investigation))
    return "\n".join(lines)
