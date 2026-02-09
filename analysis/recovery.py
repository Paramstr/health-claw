"""
analysis/recovery.py — Recovery analysis relative to Param's baselines.

Computes:
- Recovery trend (7-14d)
- Simple recovery prediction from sleep features
- Recovery vs sleep correlation strength
- Divergence detection (predicted vs actual recovery)
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
    _fetch_recovery_data,
    _fetch_sleep_data,
    _record_date,
    _extract_sleep_duration,
)

logger = logging.getLogger(__name__)

USER_ID = "32850214"


@dataclass
class RecoveryTrend:
    slope_per_day: float
    avg_7d: float
    avg_14d: Optional[float]
    direction: str  # "improving", "declining", "stable"


@dataclass
class RecoveryPrediction:
    predicted_score: float
    actual_score: float
    residual: float  # actual - predicted
    model_r_squared: float
    features_used: list[str]
    divergence_flag: Optional[str]  # "unexpectedly_low", "unexpectedly_high", None


@dataclass
class SleepRecoveryCorrelation:
    duration_corr: Optional[float]
    duration_p_value: Optional[float]
    efficiency_corr: Optional[float]
    deep_pct_corr: Optional[float]
    sample_size: int


@dataclass
class RecoveryReport:
    date: datetime
    latest_score: Optional[float]
    score_vs_baseline_pct: Optional[float]
    score_z: Optional[float]
    trend: Optional[RecoveryTrend]
    prediction: Optional[RecoveryPrediction]
    sleep_correlation: Optional[SleepRecoveryCorrelation]
    flags: list[str] = field(default_factory=list)


def _extract_recovery_score(record: dict) -> Optional[float]:
    score = record.get("score", {})
    val = score.get("recovery_score")
    return float(val) if val is not None else None


def _extract_hrv(record: dict) -> Optional[float]:
    score = record.get("score", {})
    val = score.get("hrv_rmssd_milli")
    return float(val) if val is not None else None


def _extract_rhr(record: dict) -> Optional[float]:
    score = record.get("score", {})
    val = score.get("resting_heart_rate")
    return float(val) if val is not None else None


def compute_recovery_trend(recovery_records: list[dict]) -> Optional[RecoveryTrend]:
    """Compute recovery score trend over available records."""
    scores = []
    for r in recovery_records:
        s = _extract_recovery_score(r)
        if s is not None:
            scores.append(s)

    if len(scores) < 3:
        return None

    # Reverse for chronological order (API returns newest first)
    scores_chrono = list(reversed(scores))

    avg_7d = float(np.mean(scores_chrono[-7:])) if len(scores_chrono) >= 3 else float(np.mean(scores_chrono))
    avg_14d = float(np.mean(scores_chrono[-14:])) if len(scores_chrono) >= 7 else None

    x = np.arange(len(scores_chrono))
    slope, _, _, _, _ = stats.linregress(x, scores_chrono)

    if slope > 1.0:
        direction = "improving"
    elif slope < -1.0:
        direction = "declining"
    else:
        direction = "stable"

    return RecoveryTrend(
        slope_per_day=float(slope),
        avg_7d=avg_7d,
        avg_14d=avg_14d,
        direction=direction,
    )


def compute_recovery_prediction(
    recovery_records: list[dict],
    sleep_records: list[dict],
) -> Optional[RecoveryPrediction]:
    """
    Predict today's recovery from last night's sleep features using
    Param's own recent data (simple linear model).
    """
    # Build paired dataset: sleep features -> next recovery score
    pairs = []
    for i, rec in enumerate(recovery_records):
        rec_score = _extract_recovery_score(rec)
        if rec_score is None:
            continue

        # Find matching sleep (same or closest date)
        rec_date = _record_date(rec)
        if rec_date is None:
            continue

        best_sleep = None
        best_diff = timedelta(days=999)
        for s in sleep_records:
            if s.get("nap", False):
                continue
            s_date = _record_date(s)
            if s_date is None:
                continue
            diff = abs(rec_date - s_date)
            if diff < best_diff:
                best_diff = diff
                best_sleep = s

        if best_sleep is None or best_diff > timedelta(hours=24):
            continue

        dur = _extract_sleep_duration(best_sleep)
        score = best_sleep.get("score", {})
        efficiency = score.get("sleep_efficiency_percentage")
        stages = score.get("stage_summary", {})
        total_sleep = (
            stages.get("total_light_sleep_time_milli", 0)
            + stages.get("total_slow_wave_sleep_time_milli", 0)
            + stages.get("total_rem_sleep_time_milli", 0)
        )
        deep_pct = (
            stages.get("total_slow_wave_sleep_time_milli", 0) / total_sleep * 100
            if total_sleep > 0 else None
        )

        if dur is not None and efficiency is not None and deep_pct is not None:
            pairs.append({
                "recovery": rec_score,
                "duration": dur,
                "efficiency": efficiency,
                "deep_pct": deep_pct,
            })

    if len(pairs) < 5:
        return None

    # Simple multivariate regression: recovery ~ duration + efficiency + deep_pct
    y = np.array([p["recovery"] for p in pairs])
    X = np.column_stack([
        [p["duration"] for p in pairs],
        [p["efficiency"] for p in pairs],
        [p["deep_pct"] for p in pairs],
    ])

    # Add intercept
    X_with_intercept = np.column_stack([np.ones(len(X)), X])

    try:
        # Solve least squares
        beta, residuals, rank, sv = np.linalg.lstsq(X_with_intercept, y, rcond=None)

        y_pred = X_with_intercept @ beta
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_squared = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

        # Predict for the most recent observation
        predicted = float(y_pred[0])
        actual = float(y[0])
        residual = actual - predicted

        # Flag divergence: if residual > 1.5 * RMSE
        rmse = float(np.sqrt(ss_res / len(y)))
        divergence_flag = None
        if rmse > 0:
            if residual < -1.5 * rmse:
                divergence_flag = "unexpectedly_low"
            elif residual > 1.5 * rmse:
                divergence_flag = "unexpectedly_high"

        return RecoveryPrediction(
            predicted_score=predicted,
            actual_score=actual,
            residual=residual,
            model_r_squared=r_squared,
            features_used=["sleep_duration", "sleep_efficiency", "deep_sleep_pct"],
            divergence_flag=divergence_flag,
        )
    except Exception:
        logger.exception("Recovery prediction failed")
        return None


def compute_sleep_recovery_correlation(
    recovery_records: list[dict],
    sleep_records: list[dict],
) -> Optional[SleepRecoveryCorrelation]:
    """Quantify how strongly sleep metrics predict recovery for Param."""
    # Pair up recovery scores with sleep features
    rec_scores = []
    durations = []
    efficiencies = []
    deep_pcts = []

    for rec in recovery_records:
        rec_score = _extract_recovery_score(rec)
        rec_date = _record_date(rec)
        if rec_score is None or rec_date is None:
            continue

        # Find matching sleep
        for s in sleep_records:
            if s.get("nap", False):
                continue
            s_date = _record_date(s)
            if s_date and abs(rec_date - s_date) < timedelta(hours=24):
                dur = _extract_sleep_duration(s)
                score = s.get("score", {})
                eff = score.get("sleep_efficiency_percentage")
                stages = score.get("stage_summary", {})
                total = (
                    stages.get("total_light_sleep_time_milli", 0)
                    + stages.get("total_slow_wave_sleep_time_milli", 0)
                    + stages.get("total_rem_sleep_time_milli", 0)
                )
                dp = stages.get("total_slow_wave_sleep_time_milli", 0) / total * 100 if total > 0 else None

                rec_scores.append(rec_score)
                durations.append(dur)
                efficiencies.append(eff)
                deep_pcts.append(dp)
                break

    n = len(rec_scores)
    if n < 4:
        return None

    dur_corr = dur_p = eff_corr = deep_corr = None

    valid_dur = [(r, d) for r, d in zip(rec_scores, durations) if d is not None]
    if len(valid_dur) >= 4:
        r_vals, d_vals = zip(*valid_dur)
        c, p = stats.pearsonr(d_vals, r_vals)
        dur_corr, dur_p = float(c), float(p)

    valid_eff = [(r, e) for r, e in zip(rec_scores, efficiencies) if e is not None]
    if len(valid_eff) >= 4:
        r_vals, e_vals = zip(*valid_eff)
        if np.std(e_vals) > 0 and np.std(r_vals) > 0:
            eff_corr = float(stats.pearsonr(e_vals, r_vals)[0])

    valid_deep = [(r, d) for r, d in zip(rec_scores, deep_pcts) if d is not None]
    if len(valid_deep) >= 4:
        r_vals, d_vals = zip(*valid_deep)
        if np.std(d_vals) > 0 and np.std(r_vals) > 0:
            deep_corr = float(stats.pearsonr(d_vals, r_vals)[0])

    return SleepRecoveryCorrelation(
        duration_corr=dur_corr,
        duration_p_value=dur_p,
        efficiency_corr=eff_corr,
        deep_pct_corr=deep_corr,
        sample_size=n,
    )


async def analyze_recovery(session=None) -> RecoveryReport:
    """Run full recovery analysis."""
    now = datetime.now(timezone.utc)
    recovery_records = await _fetch_recovery_data(30)
    sleep_records = await _fetch_sleep_data(30)

    flags = []

    # Latest score
    latest = None
    score_vs_baseline = None
    score_z = None

    if recovery_records:
        latest = _extract_recovery_score(recovery_records[0])

        if session and latest is not None:
            bl = await get_baseline(session, "recovery", "30d")
            if bl:
                score_vs_baseline = ((latest - bl.mean) / bl.mean) * 100
                if bl.std > 0:
                    score_z = (latest - bl.mean) / bl.std
                    if score_z < -1.5:
                        flags.append(f"Recovery significantly below baseline (z={score_z:.1f})")

    # Trend
    trend = compute_recovery_trend(recovery_records)
    if trend and trend.direction == "declining":
        flags.append(f"Recovery trending down ({trend.slope_per_day:.1f}/day over {min(14, len(recovery_records))} days)")

    # Prediction
    prediction = compute_recovery_prediction(recovery_records, sleep_records)
    if prediction and prediction.divergence_flag == "unexpectedly_low":
        flags.append(
            f"Recovery lower than expected from sleep (actual {prediction.actual_score:.0f} vs "
            f"predicted {prediction.predicted_score:.0f}) — potential stress/illness/overtraining"
        )

    # Correlation
    correlation = compute_sleep_recovery_correlation(recovery_records, sleep_records)

    return RecoveryReport(
        date=now,
        latest_score=latest,
        score_vs_baseline_pct=score_vs_baseline,
        score_z=score_z,
        trend=trend,
        prediction=prediction,
        sleep_correlation=correlation,
        flags=flags,
    )
