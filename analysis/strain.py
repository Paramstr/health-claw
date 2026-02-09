"""
analysis/strain.py — Strain and workload analysis.

Computes:
- Acute vs chronic load (7d vs 30d)
- Strain-to-recovery ratio and drift
- Overtraining detection: strain up while recovery flat/down
- Workout intensity distribution over 30d
"""

import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import stats

from analysis.baselines import (
    get_baseline,
    _fetch_cycle_data,
    _fetch_recovery_data,
    _record_date,
)
from api.whoop_client import list_records

logger = logging.getLogger(__name__)

USER_ID = "32850214"


@dataclass
class AcuteChronicLoad:
    acute_7d: float       # avg daily strain over 7d
    chronic_30d: float    # avg daily strain over 30d
    ratio: float          # acute / chronic (ACWR)
    risk_level: str       # "low", "moderate", "high"


@dataclass
class StrainRecoveryBalance:
    avg_strain_7d: float
    avg_recovery_7d: float
    strain_recovery_ratio: float
    ratio_trend: str  # "balanced", "strain_dominant", "recovery_dominant"
    drift_direction: Optional[str]  # "diverging", "converging", None


@dataclass
class OvertrainingSignal:
    detected: bool
    strain_trend_slope: float
    recovery_trend_slope: float
    days_analyzed: int
    confidence: str  # "low", "medium", "high"


@dataclass
class WorkoutDistribution:
    total_workouts: int
    avg_strain_per_workout: float
    high_intensity_count: int    # strain > 14
    moderate_count: int          # strain 8-14
    low_count: int               # strain < 8
    avg_duration_minutes: float
    sport_breakdown: dict[str, int]


@dataclass
class StrainReport:
    date: datetime
    latest_day_strain: Optional[float]
    strain_vs_baseline_pct: Optional[float]
    strain_z: Optional[float]
    acute_chronic: Optional[AcuteChronicLoad]
    balance: Optional[StrainRecoveryBalance]
    overtraining: Optional[OvertrainingSignal]
    workout_dist: Optional[WorkoutDistribution]
    flags: list[str] = field(default_factory=list)


def _extract_strain(record: dict) -> Optional[float]:
    score = record.get("score")
    if not score:
        return None
    val = score.get("strain")
    return float(val) if val is not None else None


def _extract_recovery_score(record: dict) -> Optional[float]:
    score = record.get("score", {})
    val = score.get("recovery_score")
    return float(val) if val is not None else None


async def _fetch_workout_data(days: int) -> list[dict]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    all_records = []
    next_token = None
    while True:
        data = await list_records(USER_ID, "workout", limit=25, start=start, end=end, next_token=next_token)
        records = data.get("records", [])
        all_records.extend(records)
        next_token = data.get("next_token")
        if not next_token or not records:
            break
    return all_records


def compute_acute_chronic(cycle_records: list[dict]) -> Optional[AcuteChronicLoad]:
    """Compute acute:chronic workload ratio from daily strain."""
    now = datetime.now(timezone.utc)

    strains_7d = []
    strains_30d = []

    for r in cycle_records:
        d = _record_date(r)
        strain = _extract_strain(r)
        if d is None or strain is None:
            continue
        age = (now - d).total_seconds() / 86400
        if age <= 7:
            strains_7d.append(strain)
        if age <= 30:
            strains_30d.append(strain)

    if len(strains_7d) < 2 or len(strains_30d) < 5:
        return None

    acute = float(np.mean(strains_7d))
    chronic = float(np.mean(strains_30d))

    if chronic == 0:
        return None

    ratio = acute / chronic

    if ratio < 0.8:
        risk = "low"
    elif ratio <= 1.3:
        risk = "moderate"
    else:
        risk = "high"

    return AcuteChronicLoad(
        acute_7d=acute,
        chronic_30d=chronic,
        ratio=ratio,
        risk_level=risk,
    )


def compute_strain_recovery_balance(
    cycle_records: list[dict],
    recovery_records: list[dict],
) -> Optional[StrainRecoveryBalance]:
    """Analyze balance between strain and recovery over 7 days."""
    now = datetime.now(timezone.utc)

    strains_7d = []
    for r in cycle_records:
        d = _record_date(r)
        s = _extract_strain(r)
        if d and s is not None and (now - d).total_seconds() / 86400 <= 7:
            strains_7d.append(s)

    recoveries_7d = []
    for r in recovery_records:
        d = _record_date(r)
        s = _extract_recovery_score(r)
        if d and s is not None and (now - d).total_seconds() / 86400 <= 7:
            recoveries_7d.append(s)

    if len(strains_7d) < 3 or len(recoveries_7d) < 3:
        return None

    avg_strain = float(np.mean(strains_7d))
    avg_recovery = float(np.mean(recoveries_7d))

    # Normalize strain (0-21) and recovery (0-100) to compare
    norm_strain = avg_strain / 21 * 100
    ratio = norm_strain / avg_recovery if avg_recovery > 0 else 0

    if ratio > 1.2:
        balance = "strain_dominant"
    elif ratio < 0.8:
        balance = "recovery_dominant"
    else:
        balance = "balanced"

    # Check drift: are they diverging over the 7 days?
    drift = None
    if len(strains_7d) >= 4 and len(recoveries_7d) >= 4:
        s_chrono = list(reversed(strains_7d))
        r_chrono = list(reversed(recoveries_7d))
        min_len = min(len(s_chrono), len(r_chrono))
        s_norm = np.array(s_chrono[:min_len]) / 21 * 100
        r_arr = np.array(r_chrono[:min_len])
        gap = s_norm - r_arr
        x = np.arange(len(gap))
        slope, _, _, _, _ = stats.linregress(x, gap)
        if slope > 2:
            drift = "diverging"
        elif slope < -2:
            drift = "converging"

    return StrainRecoveryBalance(
        avg_strain_7d=avg_strain,
        avg_recovery_7d=avg_recovery,
        strain_recovery_ratio=ratio,
        ratio_trend=balance,
        drift_direction=drift,
    )


def detect_overtraining(
    cycle_records: list[dict],
    recovery_records: list[dict],
    days: int = 14,
) -> Optional[OvertrainingSignal]:
    """
    Detect overtraining: strain trending up while recovery flat/declining.
    """
    now = datetime.now(timezone.utc)

    strain_series = []
    for r in cycle_records:
        d = _record_date(r)
        s = _extract_strain(r)
        if d and s is not None and (now - d).total_seconds() / 86400 <= days:
            strain_series.append((d, s))

    recovery_series = []
    for r in recovery_records:
        d = _record_date(r)
        s = _extract_recovery_score(r)
        if d and s is not None and (now - d).total_seconds() / 86400 <= days:
            recovery_series.append((d, s))

    if len(strain_series) < 5 or len(recovery_series) < 5:
        return None

    # Sort chronologically
    strain_series.sort(key=lambda x: x[0])
    recovery_series.sort(key=lambda x: x[0])

    s_vals = [v for _, v in strain_series]
    r_vals = [v for _, v in recovery_series]

    s_slope, _, _, _, _ = stats.linregress(range(len(s_vals)), s_vals)
    r_slope, _, _, _, _ = stats.linregress(range(len(r_vals)), r_vals)

    # Overtraining: strain going up (or flat high) AND recovery going down (or flat low)
    detected = bool(s_slope > 0.1 and r_slope < -0.5)

    if detected:
        if s_slope > 0.5 and r_slope < -1.0:
            confidence = "high"
        elif s_slope > 0.3 or r_slope < -0.8:
            confidence = "medium"
        else:
            confidence = "low"
    else:
        confidence = "low"

    return OvertrainingSignal(
        detected=detected,
        strain_trend_slope=float(s_slope),
        recovery_trend_slope=float(r_slope),
        days_analyzed=days,
        confidence=confidence,
    )


def compute_workout_distribution(workout_records: list[dict]) -> Optional[WorkoutDistribution]:
    """Analyze workout intensity distribution over available records."""
    if not workout_records:
        return None

    strains = []
    durations = []
    sports: dict[str, int] = {}

    for w in workout_records:
        score = w.get("score", {})
        strain = score.get("strain")
        if strain is not None:
            strains.append(float(strain))

        start = w.get("start")
        end = w.get("end")
        if start and end:
            try:
                s = datetime.fromisoformat(start.replace("Z", "+00:00"))
                e = datetime.fromisoformat(end.replace("Z", "+00:00"))
                durations.append((e - s).total_seconds() / 60)
            except ValueError:
                pass

        sport = w.get("sport_name", "unknown")
        sports[sport] = sports.get(sport, 0) + 1

    if not strains:
        return None

    return WorkoutDistribution(
        total_workouts=len(workout_records),
        avg_strain_per_workout=float(np.mean(strains)),
        high_intensity_count=sum(1 for s in strains if s > 14),
        moderate_count=sum(1 for s in strains if 8 <= s <= 14),
        low_count=sum(1 for s in strains if s < 8),
        avg_duration_minutes=float(np.mean(durations)) if durations else 0,
        sport_breakdown=sports,
    )


async def analyze_strain(session=None) -> StrainReport:
    """Run full strain analysis."""
    now = datetime.now(timezone.utc)
    cycle_records = await _fetch_cycle_data(30)
    recovery_records = await _fetch_recovery_data(30)
    workout_records = await _fetch_workout_data(30)

    flags = []

    # Latest day strain
    latest_strain = None
    strain_vs_baseline = None
    strain_z = None

    if cycle_records:
        latest_strain = _extract_strain(cycle_records[0])

        if session and latest_strain is not None:
            bl = await get_baseline(session, "strain", "30d")
            if bl:
                strain_vs_baseline = ((latest_strain - bl.mean) / bl.mean) * 100
                if bl.std > 0:
                    strain_z = (latest_strain - bl.mean) / bl.std

    # Acute:chronic
    ac = compute_acute_chronic(cycle_records)
    if ac and ac.risk_level == "high":
        flags.append(f"High acute:chronic workload ratio ({ac.ratio:.2f}) — injury risk elevated")

    # Balance
    balance = compute_strain_recovery_balance(cycle_records, recovery_records)
    if balance and balance.ratio_trend == "strain_dominant":
        flags.append("Strain outpacing recovery — consider a rest day")
    if balance and balance.drift_direction == "diverging":
        flags.append("Strain-recovery gap widening")

    # Overtraining
    ot = detect_overtraining(cycle_records, recovery_records)
    if ot and ot.detected:
        flags.append(
            f"Overtraining signal ({ot.confidence} confidence): strain trending up "
            f"(+{ot.strain_trend_slope:.1f}/day) while recovery declining ({ot.recovery_trend_slope:.1f}/day)"
        )

    # Workout distribution
    dist = compute_workout_distribution(workout_records)

    return StrainReport(
        date=now,
        latest_day_strain=latest_strain,
        strain_vs_baseline_pct=strain_vs_baseline,
        strain_z=strain_z,
        acute_chronic=ac,
        balance=balance,
        overtraining=ot,
        workout_dist=dist,
        flags=flags,
    )
