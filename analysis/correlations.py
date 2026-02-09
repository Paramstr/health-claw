"""
analysis/correlations.py — Pattern and correlation discovery.

Computes:
- Day-of-week patterns
- Bounce-back time after high strain
- Sleep duration -> next-day recovery (quantified)
- Non-obvious correlations with safeguards
"""

import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import stats

from analysis.baselines import (
    _fetch_recovery_data,
    _fetch_sleep_data,
    _fetch_cycle_data,
    _record_date,
    _extract_metric_from_recovery,
    _extract_sleep_duration,
    _extract_strain,
)

logger = logging.getLogger(__name__)

USER_ID = "32850214"

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class DayOfWeekPattern:
    metric: str
    day_averages: dict[str, float]  # day name -> avg value
    best_day: str
    worst_day: str
    significant: bool  # ANOVA p < 0.05 with enough samples


@dataclass
class BounceBack:
    avg_days_to_recover: float
    high_strain_threshold: float
    recovery_threshold: float  # what counts as "recovered"
    sample_count: int


@dataclass
class SleepRecoveryQuantified:
    correlation: float
    p_value: float
    slope: float  # recovery points per hour of sleep
    intercept: float
    r_squared: float
    sample_size: int
    interpretation: str


@dataclass
class CorrelationPair:
    metric_a: str
    metric_b: str
    correlation: float
    p_value: float
    sample_size: int
    notable: bool  # |r| > 0.3 and p < 0.05 and n >= 7


@dataclass
class CorrelationsReport:
    day_of_week: list[DayOfWeekPattern]
    bounce_back: Optional[BounceBack]
    sleep_to_recovery: Optional[SleepRecoveryQuantified]
    notable_correlations: list[CorrelationPair]


def compute_day_of_week_patterns(
    recovery_records: list[dict],
    sleep_records: list[dict],
    cycle_records: list[dict],
) -> list[DayOfWeekPattern]:
    """Find day-of-week patterns for key metrics."""
    patterns = []

    # Group by metric and day
    metric_sources = {
        "recovery": [(r, _extract_metric_from_recovery(r, "recovery")) for r in recovery_records],
        "hrv": [(r, _extract_metric_from_recovery(r, "hrv")) for r in recovery_records],
        "strain": [(r, _extract_strain(r)) for r in cycle_records],
        "sleep_duration": [(r, _extract_sleep_duration(r)) for r in sleep_records if not r.get("nap", False)],
    }

    for metric, pairs in metric_sources.items():
        by_day: dict[int, list[float]] = {i: [] for i in range(7)}
        for record, value in pairs:
            if value is None:
                continue
            d = _record_date(record)
            if d:
                by_day[d.weekday()].append(value)

        # Need at least 2 values per day for at least 3 days
        active_days = {k: v for k, v in by_day.items() if len(v) >= 2}
        if len(active_days) < 3:
            continue

        day_avgs = {}
        groups = []
        for day_idx in range(7):
            vals = by_day[day_idx]
            if vals:
                day_avgs[WEEKDAYS[day_idx]] = float(np.mean(vals))
                groups.append(vals)

        if not day_avgs:
            continue

        # ANOVA for significance
        significant = False
        if len(groups) >= 3 and all(len(g) >= 2 for g in groups):
            try:
                f_stat, p_val = stats.f_oneway(*groups)
                significant = bool(p_val < 0.05)
            except Exception:
                pass

        best = max(day_avgs, key=day_avgs.get)
        worst = min(day_avgs, key=day_avgs.get)

        patterns.append(DayOfWeekPattern(
            metric=metric,
            day_averages=day_avgs,
            best_day=best,
            worst_day=worst,
            significant=significant,
        ))

    return patterns


def compute_bounce_back(
    cycle_records: list[dict],
    recovery_records: list[dict],
    high_strain_percentile: float = 75,
) -> Optional[BounceBack]:
    """How many days until recovery returns to normal after high strain days."""
    # Build daily series
    strain_by_date: dict[str, float] = {}
    for r in cycle_records:
        d = _record_date(r)
        s = _extract_strain(r)
        if d and s is not None:
            key = d.strftime("%Y-%m-%d")
            strain_by_date[key] = s

    recovery_by_date: dict[str, float] = {}
    for r in recovery_records:
        d = _record_date(r)
        s = _extract_metric_from_recovery(r, "recovery")
        if d and s is not None:
            key = d.strftime("%Y-%m-%d")
            recovery_by_date[key] = s

    if len(strain_by_date) < 7 or len(recovery_by_date) < 7:
        return None

    all_strains = list(strain_by_date.values())
    threshold = float(np.percentile(all_strains, high_strain_percentile))
    avg_recovery = float(np.mean(list(recovery_by_date.values())))

    # Find high strain days and count days until recovery >= avg
    bounce_days = []
    sorted_dates = sorted(strain_by_date.keys())

    for date_str in sorted_dates:
        if strain_by_date[date_str] >= threshold:
            # Count days until recovery returns to average
            base_date = datetime.strptime(date_str, "%Y-%m-%d")
            for offset in range(1, 8):
                check_date = (base_date + timedelta(days=offset)).strftime("%Y-%m-%d")
                if check_date in recovery_by_date and recovery_by_date[check_date] >= avg_recovery:
                    bounce_days.append(offset)
                    break

    if len(bounce_days) < 2:
        return None

    return BounceBack(
        avg_days_to_recover=float(np.mean(bounce_days)),
        high_strain_threshold=threshold,
        recovery_threshold=avg_recovery,
        sample_count=len(bounce_days),
    )


def compute_sleep_to_recovery(
    sleep_records: list[dict],
    recovery_records: list[dict],
) -> Optional[SleepRecoveryQuantified]:
    """Quantify: each extra hour of sleep -> X recovery points."""
    pairs = []

    for rec in recovery_records:
        rec_score = _extract_metric_from_recovery(rec, "recovery")
        rec_date = _record_date(rec)
        if rec_score is None or rec_date is None:
            continue

        for s in sleep_records:
            if s.get("nap", False):
                continue
            s_date = _record_date(s)
            if s_date and abs(rec_date - s_date) < timedelta(hours=24):
                dur = _extract_sleep_duration(s)
                if dur is not None:
                    pairs.append((dur, rec_score))
                    break

    if len(pairs) < 5:
        return None

    durations, scores = zip(*pairs)
    durations = np.array(durations)
    scores = np.array(scores)

    if np.std(durations) == 0 or np.std(scores) == 0:
        return None

    slope, intercept, r, p, se = stats.linregress(durations, scores)
    r_sq = r ** 2

    if abs(r) > 0.5:
        interpretation = f"Strong relationship: each extra hour of sleep → ~{slope:.1f} recovery points"
    elif abs(r) > 0.3:
        interpretation = f"Moderate relationship: each extra hour → ~{slope:.1f} recovery points"
    else:
        interpretation = f"Weak relationship (r={r:.2f}) — other factors dominate recovery"

    return SleepRecoveryQuantified(
        correlation=float(r),
        p_value=float(p),
        slope=float(slope),
        intercept=float(intercept),
        r_squared=float(r_sq),
        sample_size=len(pairs),
        interpretation=interpretation,
    )


def find_notable_correlations(
    recovery_records: list[dict],
    sleep_records: list[dict],
    cycle_records: list[dict],
) -> list[CorrelationPair]:
    """Search for non-obvious correlations with safeguards."""
    # Build metric vectors aligned by date
    metrics_by_date: dict[str, dict[str, float]] = {}

    for r in recovery_records:
        d = _record_date(r)
        if not d:
            continue
        key = d.strftime("%Y-%m-%d")
        if key not in metrics_by_date:
            metrics_by_date[key] = {}
        for m in ["hrv", "rhr", "recovery", "spo2"]:
            v = _extract_metric_from_recovery(r, m)
            if v is not None:
                metrics_by_date[key][m] = v

    for r in sleep_records:
        if r.get("nap", False):
            continue
        d = _record_date(r)
        if not d:
            continue
        key = d.strftime("%Y-%m-%d")
        if key not in metrics_by_date:
            metrics_by_date[key] = {}
        dur = _extract_sleep_duration(r)
        if dur is not None:
            metrics_by_date[key]["sleep_duration"] = dur

    for r in cycle_records:
        d = _record_date(r)
        if not d:
            continue
        key = d.strftime("%Y-%m-%d")
        if key not in metrics_by_date:
            metrics_by_date[key] = {}
        s = _extract_strain(r)
        if s is not None:
            metrics_by_date[key]["strain"] = s

    all_metrics = ["hrv", "rhr", "recovery", "spo2", "sleep_duration", "strain"]
    notable = []

    for i, ma in enumerate(all_metrics):
        for mb in all_metrics[i + 1:]:
            pairs = []
            for day_data in metrics_by_date.values():
                if ma in day_data and mb in day_data:
                    pairs.append((day_data[ma], day_data[mb]))

            if len(pairs) < 7:  # minimum samples
                continue

            a_vals, b_vals = zip(*pairs)
            if np.std(a_vals) == 0 or np.std(b_vals) == 0:
                continue

            r, p = stats.pearsonr(a_vals, b_vals)
            is_notable = bool(abs(r) > 0.3 and p < 0.05 and len(pairs) >= 7)

            if is_notable:
                notable.append(CorrelationPair(
                    metric_a=ma,
                    metric_b=mb,
                    correlation=float(r),
                    p_value=float(p),
                    sample_size=len(pairs),
                    notable=True,
                ))

    return sorted(notable, key=lambda x: abs(x.correlation), reverse=True)


async def analyze_correlations(session=None) -> CorrelationsReport:
    """Run full correlation analysis."""
    recovery_records = await _fetch_recovery_data(30)
    sleep_records = await _fetch_sleep_data(30)
    cycle_records = await _fetch_cycle_data(30)

    dow = compute_day_of_week_patterns(recovery_records, sleep_records, cycle_records)
    bb = compute_bounce_back(cycle_records, recovery_records)
    s2r = compute_sleep_to_recovery(sleep_records, recovery_records)
    notable = find_notable_correlations(recovery_records, sleep_records, cycle_records)

    return CorrelationsReport(
        day_of_week=dow,
        bounce_back=bb,
        sleep_to_recovery=s2r,
        notable_correlations=notable,
    )
