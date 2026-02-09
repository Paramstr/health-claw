"""
analysis/investigations.py — Builds cause chains for anomalies.

Takes anomalies + recent metrics and produces natural language explanations
grounded in computed evidence (deltas, z-scores, correlations).
"""

import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from analysis.anomalies import Anomaly
from analysis.baselines import (
    BaselineResult,
    _fetch_recovery_data,
    _fetch_sleep_data,
    _fetch_cycle_data,
    _record_date,
    _extract_metric_from_recovery,
    _extract_sleep_duration,
    _extract_strain,
)

logger = logging.getLogger(__name__)


@dataclass
class CauseLink:
    """A single step in the cause chain."""
    factor: str
    evidence: str
    delta: Optional[float] = None
    z_score: Optional[float] = None
    contribution: str = "possible"  # "likely", "possible", "unlikely"


@dataclass
class Investigation:
    """Full investigation for an anomaly."""
    anomaly: Anomaly
    cause_chain: list[CauseLink]
    summary: str
    confidence: str  # "high", "medium", "low"


def investigate_low_recovery(
    anomaly: Anomaly,
    recovery_records: list[dict],
    sleep_records: list[dict],
    cycle_records: list[dict],
    baselines: dict,
) -> Investigation:
    """Investigate why recovery is low."""
    chain: list[CauseLink] = []

    # Check sleep
    if sleep_records:
        dur = _extract_sleep_duration(sleep_records[0])
        sleep_bl = baselines.get("sleep_duration", {}).get("30d")
        if dur is not None and sleep_bl:
            delta = dur - sleep_bl.mean
            z = delta / sleep_bl.std if sleep_bl.std > 0 else 0
            if delta < -0.5:
                chain.append(CauseLink(
                    factor="Short sleep",
                    evidence=f"Slept {dur:.1f}h vs {sleep_bl.mean:.1f}h baseline ({delta:+.1f}h)",
                    delta=delta,
                    z_score=z,
                    contribution="likely" if z < -1.0 else "possible",
                ))

        # Check sleep stages
        score = sleep_records[0].get("score", {})
        stages = score.get("stage_summary", {})
        total = (stages.get("total_light_sleep_time_milli", 0)
                 + stages.get("total_slow_wave_sleep_time_milli", 0)
                 + stages.get("total_rem_sleep_time_milli", 0))
        if total > 0:
            deep_pct = stages.get("total_slow_wave_sleep_time_milli", 0) / total * 100
            rem_pct = stages.get("total_rem_sleep_time_milli", 0) / total * 100
            if deep_pct < 12:
                chain.append(CauseLink(
                    factor="Low deep sleep",
                    evidence=f"Deep sleep only {deep_pct:.1f}% (below typical 15-20%)",
                    contribution="possible",
                ))
            if rem_pct < 18:
                chain.append(CauseLink(
                    factor="Low REM sleep",
                    evidence=f"REM sleep only {rem_pct:.1f}% (below typical 20-25%)",
                    contribution="possible",
                ))

    # Check prior day strain
    if cycle_records:
        strain = _extract_strain(cycle_records[0])
        strain_bl = baselines.get("strain", {}).get("30d")
        if strain is not None and strain_bl:
            delta = strain - strain_bl.mean
            z = delta / strain_bl.std if strain_bl.std > 0 else 0
            if delta > 2:
                chain.append(CauseLink(
                    factor="High prior strain",
                    evidence=f"Strain {strain:.1f} vs {strain_bl.mean:.1f} baseline ({delta:+.1f})",
                    delta=delta,
                    z_score=z,
                    contribution="likely" if z > 1.5 else "possible",
                ))

    # Check HRV
    if recovery_records:
        hrv = _extract_metric_from_recovery(recovery_records[0], "hrv")
        hrv_bl = baselines.get("hrv", {}).get("30d")
        if hrv is not None and hrv_bl:
            delta = hrv - hrv_bl.mean
            z = delta / hrv_bl.std if hrv_bl.std > 0 else 0
            if z < -1.0:
                chain.append(CauseLink(
                    factor="HRV suppressed",
                    evidence=f"HRV {hrv:.1f}ms vs {hrv_bl.mean:.1f}ms baseline (z={z:.1f})",
                    delta=delta,
                    z_score=z,
                    contribution="likely",
                ))

    # Check RHR
    if recovery_records:
        rhr = _extract_metric_from_recovery(recovery_records[0], "rhr")
        rhr_bl = baselines.get("rhr", {}).get("30d")
        if rhr is not None and rhr_bl:
            delta = rhr - rhr_bl.mean
            z = delta / rhr_bl.std if rhr_bl.std > 0 else 0
            if z > 1.0:
                chain.append(CauseLink(
                    factor="Elevated resting HR",
                    evidence=f"RHR {rhr:.0f} vs {rhr_bl.mean:.0f} baseline (z={z:.1f})",
                    delta=delta,
                    z_score=z,
                    contribution="possible",
                ))

    # Build summary
    likely = [c for c in chain if c.contribution == "likely"]
    possible = [c for c in chain if c.contribution == "possible"]

    if likely:
        summary_parts = [f"Low recovery likely driven by: {', '.join(c.factor.lower() for c in likely)}."]
        if possible:
            summary_parts.append(f"Additional factors: {', '.join(c.factor.lower() for c in possible)}.")
        confidence = "high" if len(likely) >= 2 else "medium"
    elif possible:
        summary_parts = [f"Low recovery possibly due to: {', '.join(c.factor.lower() for c in possible)}."]
        confidence = "low"
    else:
        summary_parts = ["Low recovery with no clear single cause — may indicate accumulated fatigue, stress, or illness."]
        confidence = "low"

    return Investigation(
        anomaly=anomaly,
        cause_chain=chain,
        summary=" ".join(summary_parts),
        confidence=confidence,
    )


def investigate_high_strain(
    anomaly: Anomaly,
    cycle_records: list[dict],
    baselines: dict,
) -> Investigation:
    """Investigate unusually high strain."""
    chain: list[CauseLink] = []
    strain_bl = baselines.get("strain", {}).get("30d")

    if cycle_records and strain_bl:
        strain = _extract_strain(cycle_records[0])
        if strain is not None:
            # Check if there were multiple workouts
            recent_strains = [_extract_strain(r) for r in cycle_records[:3]]
            recent_strains = [s for s in recent_strains if s is not None]
            if len(recent_strains) >= 2 and all(s > strain_bl.mean * 1.2 for s in recent_strains[:2]):
                chain.append(CauseLink(
                    factor="Consecutive high-strain days",
                    evidence=f"Last {len(recent_strains)} days all above baseline ({', '.join(f'{s:.1f}' for s in recent_strains)})",
                    contribution="likely",
                ))

    summary = "Elevated strain" + (f" — {chain[0].evidence}" if chain else " relative to recent norms.")
    return Investigation(
        anomaly=anomaly,
        cause_chain=chain,
        summary=summary,
        confidence="medium" if chain else "low",
    )


def investigate_spo2_drop(anomaly: Anomaly) -> Investigation:
    """Investigate SpO2 drop."""
    chain = [
        CauseLink(
            factor="SpO2 below normal",
            evidence=f"SpO2 at {anomaly.current_value:.1f}%",
            contribution="likely",
        ),
        CauseLink(
            factor="Possible causes",
            evidence="Sleep apnea, altitude, respiratory illness, or sensor positioning",
            contribution="possible",
        ),
    ]
    return Investigation(
        anomaly=anomaly,
        cause_chain=chain,
        summary=f"SpO2 dropped to {anomaly.current_value:.1f}% — monitor closely. If sustained, consider medical evaluation.",
        confidence="medium",
    )


def investigate_generic(anomaly: Anomaly) -> Investigation:
    """Generic investigation for anomalies without specific handlers."""
    return Investigation(
        anomaly=anomaly,
        cause_chain=[CauseLink(
            factor=f"{anomaly.metric} anomaly",
            evidence=anomaly.description,
            contribution="possible",
        )],
        summary=anomaly.description,
        confidence="low",
    )


async def investigate_anomalies(
    anomalies: list[Anomaly],
    baselines: dict = None,
) -> list[Investigation]:
    """
    Investigate all anomalies and build cause chains.
    Fetches data as needed.
    """
    if not anomalies:
        return []

    recovery_records = await _fetch_recovery_data(7)
    sleep_records = await _fetch_sleep_data(7)
    cycle_records = await _fetch_cycle_data(7)
    baselines = baselines or {}

    investigations = []
    for anomaly in anomalies:
        if anomaly.metric == "recovery" and anomaly.direction == "below":
            inv = investigate_low_recovery(anomaly, recovery_records, sleep_records, cycle_records, baselines)
        elif anomaly.metric == "strain" and anomaly.direction == "above":
            inv = investigate_high_strain(anomaly, cycle_records, baselines)
        elif anomaly.metric == "spo2" and anomaly.direction == "below":
            inv = investigate_spo2_drop(anomaly)
        else:
            inv = investigate_generic(anomaly)
        investigations.append(inv)

    return investigations
