"""
analysis/sleep.py — Sleep analysis relative to Param's baselines.

Computes:
- Trend over 7-14d (slope/deltas)
- Stage distribution vs personal norm (REM%, deep%, light%)
- Sleep consistency: bedtime/wake variance and correlation with recovery
- Sleep debt accumulation/repayment model
- Detect unusually low REM or deep relative to baseline (z-score)
"""

import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import stats

from analysis.baselines import (
    BaselineResult,
    get_baseline,
    _extract_sleep_duration,
    _record_date,
    _fetch_sleep_data,
    _fetch_recovery_data,
)

logger = logging.getLogger(__name__)

USER_ID = "32850214"


@dataclass
class SleepStages:
    light_pct: float
    deep_pct: float
    rem_pct: float
    awake_pct: float
    total_sleep_ms: int
    total_in_bed_ms: int


@dataclass
class SleepTrend:
    duration_slope_hrs_per_day: float  # positive = improving
    duration_7d_avg_hrs: float
    duration_14d_avg_hrs: Optional[float]
    direction: str  # "improving", "declining", "stable"


@dataclass
class StageAnalysis:
    current_rem_pct: float
    current_deep_pct: float
    current_light_pct: float
    baseline_rem_pct: Optional[float]
    baseline_deep_pct: Optional[float]
    baseline_light_pct: Optional[float]
    rem_z_score: Optional[float]
    deep_z_score: Optional[float]
    rem_flag: Optional[str]  # "low", "high", None
    deep_flag: Optional[str]


@dataclass
class ConsistencyAnalysis:
    bedtime_std_minutes: float
    waketime_std_minutes: float
    consistency_score: float  # 0-100, higher = more consistent
    consistency_recovery_correlation: Optional[float]


@dataclass
class SleepDebt:
    baseline_need_hrs: float
    current_debt_hrs: float  # positive = in debt
    trend: str  # "accumulating", "repaying", "stable"
    days_analyzed: int


@dataclass
class SleepReport:
    """Full sleep analysis output."""
    date: datetime
    last_sleep_duration_hrs: Optional[float]
    duration_vs_baseline_pct: Optional[float]  # e.g. -12.5 means 12.5% below
    duration_z_score: Optional[float]
    efficiency_pct: Optional[float]
    trend: Optional[SleepTrend]
    stages: Optional[StageAnalysis]
    consistency: Optional[ConsistencyAnalysis]
    debt: Optional[SleepDebt]
    flags: list[str] = field(default_factory=list)


def _extract_stages(record: dict) -> Optional[SleepStages]:
    """Extract sleep stage breakdown from a sleep record."""
    score = record.get("score")
    if not score:
        return None
    summary = score.get("stage_summary")
    if not summary:
        return None

    light = summary.get("total_light_sleep_time_milli", 0)
    deep = summary.get("total_slow_wave_sleep_time_milli", 0)
    rem = summary.get("total_rem_sleep_time_milli", 0)
    awake = summary.get("total_awake_time_milli", 0)
    in_bed = summary.get("total_in_bed_time_milli", 0)
    total_sleep = light + deep + rem

    if total_sleep == 0:
        return None

    return SleepStages(
        light_pct=light / total_sleep * 100,
        deep_pct=deep / total_sleep * 100,
        rem_pct=rem / total_sleep * 100,
        awake_pct=awake / in_bed * 100 if in_bed > 0 else 0,
        total_sleep_ms=total_sleep,
        total_in_bed_ms=in_bed,
    )


def _extract_bedtime_waketime(record: dict) -> tuple[Optional[float], Optional[float]]:
    """Extract bedtime and wake time as hour-of-day floats (0-24)."""
    start = record.get("start")
    end = record.get("end")
    offset = record.get("timezone_offset", "+00:00")

    if not start or not end:
        return None, None

    try:
        # Parse offset like "+11:00" to hours
        sign = 1 if offset.startswith("+") else -1
        parts = offset.lstrip("+-").split(":")
        offset_hrs = sign * (int(parts[0]) + int(parts[1]) / 60)

        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))

        # Convert to local time
        local_bed = (start_dt.hour + start_dt.minute / 60 + offset_hrs) % 24
        local_wake = (end_dt.hour + end_dt.minute / 60 + offset_hrs) % 24

        return local_bed, local_wake
    except (ValueError, IndexError):
        return None, None


def compute_sleep_trend(sleep_records: list[dict]) -> Optional[SleepTrend]:
    """Compute sleep duration trend over available records."""
    durations = []
    for r in sleep_records:
        if r.get("nap", False):
            continue
        dur = _extract_sleep_duration(r)
        if dur is not None:
            durations.append(dur)

    if len(durations) < 3:
        return None

    # Most recent first in API, reverse for chronological
    durations = list(reversed(durations))

    avg_7d = float(np.mean(durations[:7])) if len(durations) >= 3 else float(np.mean(durations))
    avg_14d = float(np.mean(durations[:14])) if len(durations) >= 7 else None

    # Linear regression for slope
    x = np.arange(len(durations))
    slope, _, _, _, _ = stats.linregress(x, durations)

    if slope > 0.05:
        direction = "improving"
    elif slope < -0.05:
        direction = "declining"
    else:
        direction = "stable"

    return SleepTrend(
        duration_slope_hrs_per_day=float(slope),
        duration_7d_avg_hrs=avg_7d,
        duration_14d_avg_hrs=avg_14d,
        direction=direction,
    )


def compute_stage_analysis(
    sleep_records: list[dict],
) -> Optional[StageAnalysis]:
    """Analyze sleep stage distribution vs personal norm."""
    all_stages = []
    for r in sleep_records:
        if r.get("nap", False):
            continue
        s = _extract_stages(r)
        if s:
            all_stages.append(s)

    if len(all_stages) < 2:
        return None

    current = all_stages[0]  # most recent
    rem_pcts = [s.rem_pct for s in all_stages]
    deep_pcts = [s.deep_pct for s in all_stages]
    light_pcts = [s.light_pct for s in all_stages]

    baseline_rem = float(np.mean(rem_pcts[1:]))  # exclude current
    baseline_deep = float(np.mean(deep_pcts[1:]))
    baseline_light = float(np.mean(light_pcts[1:]))

    rem_std = float(np.std(rem_pcts[1:], ddof=1)) if len(rem_pcts) > 2 else None
    deep_std = float(np.std(deep_pcts[1:], ddof=1)) if len(deep_pcts) > 2 else None

    rem_z = (current.rem_pct - baseline_rem) / rem_std if rem_std and rem_std > 0 else None
    deep_z = (current.deep_pct - baseline_deep) / deep_std if deep_std and deep_std > 0 else None

    rem_flag = None
    if rem_z is not None:
        if rem_z < -1.5:
            rem_flag = "low"
        elif rem_z > 1.5:
            rem_flag = "high"

    deep_flag = None
    if deep_z is not None:
        if deep_z < -1.5:
            deep_flag = "low"
        elif deep_z > 1.5:
            deep_flag = "high"

    return StageAnalysis(
        current_rem_pct=current.rem_pct,
        current_deep_pct=current.deep_pct,
        current_light_pct=current.light_pct,
        baseline_rem_pct=baseline_rem,
        baseline_deep_pct=baseline_deep,
        baseline_light_pct=baseline_light,
        rem_z_score=rem_z,
        deep_z_score=deep_z,
        rem_flag=rem_flag,
        deep_flag=deep_flag,
    )


def compute_consistency(
    sleep_records: list[dict],
    recovery_records: Optional[list[dict]] = None,
) -> Optional[ConsistencyAnalysis]:
    """Analyze sleep timing consistency."""
    bedtimes = []
    waketimes = []

    for r in sleep_records:
        if r.get("nap", False):
            continue
        bed, wake = _extract_bedtime_waketime(r)
        if bed is not None and wake is not None:
            bedtimes.append(bed)
            waketimes.append(wake)

    if len(bedtimes) < 3:
        return None

    # Handle wrap-around for bedtimes (e.g., 23:00 and 01:00)
    bed_arr = np.array(bedtimes)
    wake_arr = np.array(waketimes)

    # Use circular std for bedtimes (they can wrap around midnight)
    bed_radians = bed_arr * (2 * np.pi / 24)
    R = np.sqrt(np.mean(np.cos(bed_radians))**2 + np.mean(np.sin(bed_radians))**2)
    bed_std_hrs = np.sqrt(-2 * np.log(max(R, 0.01))) * 24 / (2 * np.pi)
    bed_std_min = bed_std_hrs * 60

    wake_std_min = float(np.std(wake_arr, ddof=1) * 60)

    # Consistency score: 100 = perfect, lower = more variable
    # Use combined std, cap at 120 min for 0 score
    combined_std = (bed_std_min + wake_std_min) / 2
    consistency_score = max(0, 100 - (combined_std / 120) * 100)

    # Correlation with recovery if available
    corr = None
    if recovery_records and len(recovery_records) >= 3:
        recovery_scores = []
        for rec in recovery_records[:len(bedtimes)]:
            score = rec.get("score", {})
            rs = score.get("recovery_score")
            if rs is not None:
                recovery_scores.append(float(rs))
        if len(recovery_scores) >= 3 and len(recovery_scores) == len(bedtimes[:len(recovery_scores)]):
            # Use consistency score proxy: deviation from mean bedtime
            bed_devs = np.abs(bed_arr[:len(recovery_scores)] - np.mean(bed_arr[:len(recovery_scores)]))
            if np.std(bed_devs) > 0:
                corr_val, _ = stats.pearsonr(bed_devs, recovery_scores[:len(bed_devs)])
                corr = float(corr_val)

    return ConsistencyAnalysis(
        bedtime_std_minutes=float(bed_std_min),
        waketime_std_minutes=wake_std_min,
        consistency_score=float(consistency_score),
        consistency_recovery_correlation=corr,
    )


def compute_sleep_debt(
    sleep_records: list[dict],
    baseline_need_hrs: float = 7.8,  # default, will use WHOOP's baseline if available
) -> Optional[SleepDebt]:
    """Compute accumulated sleep debt over recent period."""
    durations = []
    for r in sleep_records:
        if r.get("nap", False):
            continue
        dur = _extract_sleep_duration(r)
        if dur is not None:
            durations.append(dur)

    if len(durations) < 3:
        return None

    # Use WHOOP's baseline need if available from the most recent record
    score = sleep_records[0].get("score", {})
    need = score.get("sleep_needed", {})
    whoop_baseline = need.get("baseline_milli")
    if whoop_baseline:
        baseline_need_hrs = whoop_baseline / 3_600_000

    # Compute debt: sum of (need - actual) over last 7 days
    recent = durations[:7]
    debt = sum(baseline_need_hrs - d for d in recent)

    # Trend: is debt growing or shrinking?
    if len(durations) >= 5:
        recent_debt = sum(baseline_need_hrs - d for d in durations[:3])
        older_debt = sum(baseline_need_hrs - d for d in durations[3:6])
        if recent_debt > older_debt + 0.5:
            trend = "accumulating"
        elif recent_debt < older_debt - 0.5:
            trend = "repaying"
        else:
            trend = "stable"
    else:
        trend = "stable"

    return SleepDebt(
        baseline_need_hrs=baseline_need_hrs,
        current_debt_hrs=float(debt),
        trend=trend,
        days_analyzed=len(recent),
    )


async def analyze_sleep(session=None) -> SleepReport:
    """
    Run full sleep analysis. Returns a SleepReport.
    If session is provided, reads baselines from DB.
    """
    now = datetime.now(timezone.utc)
    sleep_records = await _fetch_sleep_data(14)
    recovery_records = await _fetch_recovery_data(14)

    # Filter out naps for primary analysis
    main_sleeps = [r for r in sleep_records if not r.get("nap", False)]

    flags = []

    # Latest sleep stats
    last_duration = None
    duration_vs_baseline = None
    duration_z = None
    efficiency = None

    if main_sleeps:
        last_duration = _extract_sleep_duration(main_sleeps[0])
        score = main_sleeps[0].get("score", {})
        efficiency = score.get("sleep_efficiency_percentage")

        # Compare to baseline if available
        if session:
            bl = await get_baseline(session, "sleep_duration", "30d")
            if bl and last_duration is not None:
                duration_vs_baseline = ((last_duration - bl.mean) / bl.mean) * 100
                if bl.std > 0:
                    duration_z = (last_duration - bl.mean) / bl.std
                    if duration_z < -1.5:
                        flags.append(f"Sleep duration significantly below baseline (z={duration_z:.1f})")

    # Trend
    trend = compute_sleep_trend(sleep_records)
    if trend and trend.direction == "declining":
        flags.append(f"Sleep duration declining (slope: {trend.duration_slope_hrs_per_day:.2f} hrs/day)")

    # Stages
    stages = compute_stage_analysis(sleep_records)
    if stages:
        if stages.rem_flag == "low":
            flags.append(f"REM sleep unusually low ({stages.current_rem_pct:.1f}% vs {stages.baseline_rem_pct:.1f}% norm, z={stages.rem_z_score:.1f})")
        if stages.deep_flag == "low":
            flags.append(f"Deep sleep unusually low ({stages.current_deep_pct:.1f}% vs {stages.baseline_deep_pct:.1f}% norm, z={stages.deep_z_score:.1f})")

    # Consistency
    consistency = compute_consistency(sleep_records, recovery_records)
    if consistency and consistency.consistency_score < 50:
        flags.append(f"Sleep timing inconsistent (score: {consistency.consistency_score:.0f}/100)")

    # Debt
    debt = compute_sleep_debt(sleep_records)
    if debt and debt.current_debt_hrs > 5:
        flags.append(f"Significant sleep debt: {debt.current_debt_hrs:.1f} hrs over {debt.days_analyzed} days")

    return SleepReport(
        date=now,
        last_sleep_duration_hrs=last_duration,
        duration_vs_baseline_pct=duration_vs_baseline,
        duration_z_score=duration_z,
        efficiency_pct=efficiency,
        trend=trend,
        stages=stages,
        consistency=consistency,
        debt=debt,
        flags=flags,
    )
