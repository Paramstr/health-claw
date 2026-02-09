"""
analysis/anomalies.py — Z-score based anomaly detection.

Severity levels:
- info:  1.5-2.0 std
- watch: 2.0-2.5 std or sustained 2+ days
- alert: >2.5 std or sustained + multi-signal combo

Special checks:
- SpO2 drops
- RHR trending up 3+ days
- HRV suppression despite adequate sleep
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from analysis.baselines import (
    BaselineResult,
    get_all_baselines,
    _fetch_recovery_data,
    _fetch_sleep_data,
    _fetch_cycle_data,
    _record_date,
    _extract_metric_from_recovery,
    _extract_sleep_duration,
    _extract_strain,
    METRICS,
)

logger = logging.getLogger(__name__)

USER_ID = "32850214"


@dataclass
class Anomaly:
    metric: str
    severity: str           # "info", "watch", "alert"
    z_score: Optional[float]
    current_value: float
    baseline_mean: float
    baseline_std: float
    direction: str          # "above", "below"
    sustained_days: int     # how many consecutive days anomalous
    description: str
    fingerprint: str        # for dedup


def _compute_fingerprint(metric: str, severity: str, direction: str) -> str:
    """Generate a stable fingerprint for dedup."""
    raw = f"{metric}:{severity}:{direction}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _severity_from_z(z: float, sustained_days: int = 1, multi_signal: bool = False) -> str:
    """Determine severity from z-score and context."""
    abs_z = abs(z)
    if abs_z > 2.5 or (sustained_days >= 2 and multi_signal):
        return "alert"
    elif abs_z > 2.0 or sustained_days >= 2:
        return "watch"
    elif abs_z > 1.5:
        return "info"
    return "none"


def detect_z_score_anomalies(
    metric: str,
    values: list[tuple[datetime, float]],
    baseline: Optional[BaselineResult],
) -> list[Anomaly]:
    """Detect anomalies for a single metric using z-scores against baseline."""
    if not baseline or baseline.std == 0 or not values:
        return []

    anomalies = []
    # Sort chronologically (newest first for sustained check)
    sorted_vals = sorted(values, key=lambda x: x[0], reverse=True)

    # Check most recent value
    latest_date, latest_val = sorted_vals[0]
    z = (latest_val - baseline.mean) / baseline.std
    direction = "above" if z > 0 else "below"

    # Count sustained anomalous days
    sustained = 0
    for _, val in sorted_vals:
        vz = abs((val - baseline.mean) / baseline.std)
        if vz > 1.5:
            sustained += 1
        else:
            break

    severity = _severity_from_z(z, sustained)
    if severity != "none":
        anomalies.append(Anomaly(
            metric=metric,
            severity=severity,
            z_score=float(z),
            current_value=float(latest_val),
            baseline_mean=baseline.mean,
            baseline_std=baseline.std,
            direction=direction,
            sustained_days=sustained,
            description=_describe_anomaly(metric, float(z), float(latest_val), baseline.mean, direction, sustained),
            fingerprint=_compute_fingerprint(metric, severity, direction),
        ))

    return anomalies


def _describe_anomaly(metric: str, z: float, value: float, mean: float, direction: str, sustained: int) -> str:
    """Generate human-readable anomaly description."""
    metric_names = {
        "hrv": "HRV",
        "rhr": "Resting HR",
        "sleep_duration": "Sleep duration",
        "strain": "Strain",
        "recovery": "Recovery",
        "spo2": "SpO2",
    }
    name = metric_names.get(metric, metric)
    delta_pct = ((value - mean) / mean) * 100

    parts = [f"{name}: {value:.1f} ({delta_pct:+.1f}% vs baseline {mean:.1f})"]
    parts.append(f"z-score: {z:.2f}")
    if sustained > 1:
        parts.append(f"sustained {sustained} days")
    return " | ".join(parts)


def detect_spo2_drop(recovery_records: list[dict]) -> list[Anomaly]:
    """Special check: SpO2 dropping below safe levels."""
    anomalies = []
    for r in recovery_records[:3]:  # Check last 3 days
        spo2 = _extract_metric_from_recovery(r, "spo2")
        if spo2 is not None and spo2 < 94.0:
            severity = "alert" if spo2 < 92.0 else "watch"
            d = _record_date(r)
            date_str = d.strftime("%m/%d") if d else "recent"
            anomalies.append(Anomaly(
                metric="spo2",
                severity=severity,
                z_score=None,
                current_value=float(spo2),
                baseline_mean=95.0,
                baseline_std=1.5,
                direction="below",
                sustained_days=1,
                description=f"SpO2 dropped to {spo2:.1f}% on {date_str} — below safe threshold",
                fingerprint=_compute_fingerprint("spo2_drop", severity, "below"),
            ))
            break  # One alert is enough
    return anomalies


def detect_rhr_trend(recovery_records: list[dict]) -> list[Anomaly]:
    """Special check: RHR trending up for 3+ consecutive days."""
    rhr_vals = []
    for r in recovery_records:
        rhr = _extract_metric_from_recovery(r, "rhr")
        d = _record_date(r)
        if rhr is not None and d is not None:
            rhr_vals.append((d, rhr))

    if len(rhr_vals) < 3:
        return []

    rhr_vals.sort(key=lambda x: x[0], reverse=True)

    # Count consecutive days of increasing RHR
    increasing_days = 0
    for i in range(len(rhr_vals) - 1):
        if rhr_vals[i][1] > rhr_vals[i + 1][1]:
            increasing_days += 1
        else:
            break

    if increasing_days >= 3:
        current = rhr_vals[0][1]
        three_days_ago = rhr_vals[min(3, len(rhr_vals) - 1)][1]
        return [Anomaly(
            metric="rhr",
            severity="watch" if increasing_days < 5 else "alert",
            z_score=None,
            current_value=float(current),
            baseline_mean=float(three_days_ago),
            baseline_std=0,
            direction="above",
            sustained_days=increasing_days,
            description=f"RHR rising for {increasing_days} consecutive days ({three_days_ago:.0f} → {current:.0f} bpm)",
            fingerprint=_compute_fingerprint("rhr_trend", "watch", "above"),
        )]
    return []


def detect_hrv_suppression(
    recovery_records: list[dict],
    sleep_records: list[dict],
    baselines: dict,
) -> list[Anomaly]:
    """
    Special check: HRV suppressed despite adequate sleep.
    This may indicate stress, illness, or overtraining.
    """
    if not recovery_records or not sleep_records:
        return []

    # Check if latest HRV is low
    latest_hrv = _extract_metric_from_recovery(recovery_records[0], "hrv")
    hrv_bl = baselines.get("hrv", {}).get("30d")
    if latest_hrv is None or hrv_bl is None or hrv_bl.std == 0:
        return []

    hrv_z = (latest_hrv - hrv_bl.mean) / hrv_bl.std
    if hrv_z >= -1.0:
        return []  # HRV is fine

    # Check if sleep was adequate
    sleep_bl = baselines.get("sleep_duration", {}).get("30d")
    latest_sleep_dur = _extract_sleep_duration(sleep_records[0]) if sleep_records else None
    if latest_sleep_dur is None or sleep_bl is None:
        return []

    sleep_z = (latest_sleep_dur - sleep_bl.mean) / sleep_bl.std if sleep_bl.std > 0 else 0
    if sleep_z < -0.5:
        return []  # Sleep was also short, HRV suppression explained

    return [Anomaly(
        metric="hrv",
        severity="watch",
        z_score=float(hrv_z),
        current_value=float(latest_hrv),
        baseline_mean=hrv_bl.mean,
        baseline_std=hrv_bl.std,
        direction="below",
        sustained_days=1,
        description=(
            f"HRV suppressed ({latest_hrv:.1f}ms, z={hrv_z:.1f}) despite adequate sleep "
            f"({latest_sleep_dur:.1f}h) — possible stress, illness, or overtraining"
        ),
        fingerprint=_compute_fingerprint("hrv_suppression", "watch", "below"),
    )]


async def detect_all_anomalies(session=None) -> list[Anomaly]:
    """
    Run all anomaly detection checks.
    If session provided, reads baselines from DB. Otherwise computes inline.
    """
    recovery_records = await _fetch_recovery_data(14)
    sleep_records = await _fetch_sleep_data(14)
    cycle_records = await _fetch_cycle_data(14)

    all_anomalies: list[Anomaly] = []

    # Get baselines
    baselines: dict = {}
    if session:
        baselines = await get_all_baselines(session)

    # Z-score anomalies for each metric
    now = datetime.now(timezone.utc)

    # Recovery metrics
    for metric in ["hrv", "rhr", "recovery", "spo2"]:
        values = []
        for r in recovery_records:
            d = _record_date(r)
            v = _extract_metric_from_recovery(r, metric)
            if d and v is not None:
                values.append((d, v))
        bl = baselines.get(metric, {}).get("30d")
        all_anomalies.extend(detect_z_score_anomalies(metric, values, bl))

    # Sleep duration
    sleep_values = []
    for r in sleep_records:
        if r.get("nap", False):
            continue
        d = _record_date(r)
        v = _extract_sleep_duration(r)
        if d and v is not None:
            sleep_values.append((d, v))
    bl = baselines.get("sleep_duration", {}).get("30d")
    all_anomalies.extend(detect_z_score_anomalies("sleep_duration", sleep_values, bl))

    # Strain
    strain_values = []
    for r in cycle_records:
        d = _record_date(r)
        v = _extract_strain(r)
        if d and v is not None:
            strain_values.append((d, v))
    bl = baselines.get("strain", {}).get("30d")
    all_anomalies.extend(detect_z_score_anomalies("strain", strain_values, bl))

    # Special checks
    all_anomalies.extend(detect_spo2_drop(recovery_records))
    all_anomalies.extend(detect_rhr_trend(recovery_records))
    all_anomalies.extend(detect_hrv_suppression(recovery_records, sleep_records, baselines))

    # Deduplicate by fingerprint (keep highest severity)
    seen: dict[str, Anomaly] = {}
    severity_order = {"alert": 3, "watch": 2, "info": 1, "none": 0}
    for a in all_anomalies:
        existing = seen.get(a.fingerprint)
        if not existing or severity_order.get(a.severity, 0) > severity_order.get(existing.severity, 0):
            seen[a.fingerprint] = a

    return sorted(seen.values(), key=lambda a: severity_order.get(a.severity, 0), reverse=True)
