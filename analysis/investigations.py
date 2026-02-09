"""
analysis/investigations.py — Root cause investigation from anomalies.

Given anomalies, looks back through recent data to build cause-chains:
- What happened before the anomaly?
- Are multiple signals converging?
- What's the likely explanation?
"""

import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from analysis.anomalies import Anomaly
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


@dataclass
class CauseLink:
    """A single link in a cause chain."""
    metric: str
    observation: str
    days_prior: int  # 0 = same day, 1 = yesterday, etc.
    value: Optional[float] = None
    context: Optional[str] = None


@dataclass
class Investigation:
    """Root cause investigation for an anomaly."""
    anomaly: Anomaly
    hypothesis: str
    confidence: str  # "high", "medium", "low"
    cause_chain: list[CauseLink]
    recommendation: str
    supporting_signals: int  # how many signals agree


# --- Cause templates ---

HYPOTHESES = {
    "overtraining": {
        "pattern": "high strain + low HRV + high RHR",
        "recommendation": "Consider a rest or active recovery day. Your body is signaling accumulated fatigue.",
    },
    "sleep_debt": {
        "pattern": "short sleep + low recovery + low HRV",
        "recommendation": "Prioritize sleep tonight — aim for 8+ hours. Avoid screens 1h before bed.",
    },
    "illness_onset": {
        "pattern": "low SpO2 + high RHR + low HRV + adequate sleep",
        "recommendation": "Monitor closely. If SpO2 stays below 94% or you feel symptomatic, consider seeing a doctor.",
    },
    "stress_response": {
        "pattern": "low HRV + normal sleep + low/moderate strain",
        "recommendation": "HRV suppressed despite rest — likely mental/emotional stress. Consider breathing exercises or meditation.",
    },
    "bounce_back": {
        "pattern": "recovery improving after strain spike",
        "recommendation": "Your body is recovering well. Maintain current approach.",
    },
    "acute_strain": {
        "pattern": "unusually high strain day",
        "recommendation": "Expect lower recovery tomorrow. Prioritize sleep and hydration tonight.",
    },
}


def _get_recent_context(
    recovery_records: list[dict],
    sleep_records: list[dict],
    cycle_records: list[dict],
    lookback_days: int = 5,
) -> dict:
    """Build a daily context map for the last N days."""
    context: dict[str, dict] = {}

    for r in recovery_records:
        d = _record_date(r)
        if not d:
            continue
        key = d.strftime("%Y-%m-%d")
        if key not in context:
            context[key] = {}
        context[key]["hrv"] = _extract_metric_from_recovery(r, "hrv")
        context[key]["rhr"] = _extract_metric_from_recovery(r, "rhr")
        context[key]["recovery"] = _extract_metric_from_recovery(r, "recovery")
        context[key]["spo2"] = _extract_metric_from_recovery(r, "spo2")

    for r in sleep_records:
        if r.get("nap", False):
            continue
        d = _record_date(r)
        if not d:
            continue
        key = d.strftime("%Y-%m-%d")
        if key not in context:
            context[key] = {}
        context[key]["sleep_duration"] = _extract_sleep_duration(r)

    for r in cycle_records:
        d = _record_date(r)
        if not d:
            continue
        key = d.strftime("%Y-%m-%d")
        if key not in context:
            context[key] = {}
        context[key]["strain"] = _extract_strain(r)

    return context


def _check_overtraining(anomaly: Anomaly, context: dict) -> Optional[Investigation]:
    """Check if anomaly fits overtraining pattern."""
    if anomaly.metric not in ("hrv", "rhr", "recovery"):
        return None

    sorted_dates = sorted(context.keys(), reverse=True)
    if not sorted_dates:
        return None

    chain = []
    supporting = 0

    # Look for high strain in recent days
    for i, date_str in enumerate(sorted_dates[:5]):
        day = context[date_str]
        strain = day.get("strain")
        if strain is not None and strain > 14:
            chain.append(CauseLink(
                metric="strain",
                observation=f"High strain ({strain:.1f})",
                days_prior=i,
                value=strain,
            ))
            supporting += 1

    # Low HRV
    latest = context.get(sorted_dates[0], {})
    hrv = latest.get("hrv")
    if hrv is not None:
        chain.append(CauseLink(
            metric="hrv",
            observation=f"HRV at {hrv:.1f}ms",
            days_prior=0,
            value=hrv,
        ))

    # High RHR
    rhr = latest.get("rhr")
    if rhr is not None:
        chain.append(CauseLink(
            metric="rhr",
            observation=f"RHR at {rhr:.0f} bpm",
            days_prior=0,
            value=rhr,
        ))

    if supporting >= 1 and len(chain) >= 2:
        return Investigation(
            anomaly=anomaly,
            hypothesis="overtraining",
            confidence="medium" if supporting >= 2 else "low",
            cause_chain=chain,
            recommendation=HYPOTHESES["overtraining"]["recommendation"],
            supporting_signals=supporting + len(chain),
        )
    return None


def _check_sleep_debt(anomaly: Anomaly, context: dict) -> Optional[Investigation]:
    """Check if anomaly fits sleep debt pattern."""
    if anomaly.metric not in ("recovery", "hrv", "sleep_duration"):
        return None

    sorted_dates = sorted(context.keys(), reverse=True)
    if not sorted_dates:
        return None

    chain = []
    short_sleep_days = 0

    for i, date_str in enumerate(sorted_dates[:5]):
        day = context[date_str]
        sleep = day.get("sleep_duration")
        if sleep is not None and sleep < 6.5:
            chain.append(CauseLink(
                metric="sleep_duration",
                observation=f"Short sleep ({sleep:.1f}h)",
                days_prior=i,
                value=sleep,
            ))
            short_sleep_days += 1

    if short_sleep_days >= 2:
        latest = context.get(sorted_dates[0], {})
        recovery = latest.get("recovery")
        if recovery is not None:
            chain.append(CauseLink(
                metric="recovery",
                observation=f"Recovery at {recovery:.0f}%",
                days_prior=0,
                value=recovery,
            ))

        return Investigation(
            anomaly=anomaly,
            hypothesis="sleep_debt",
            confidence="high" if short_sleep_days >= 3 else "medium",
            cause_chain=chain,
            recommendation=HYPOTHESES["sleep_debt"]["recommendation"],
            supporting_signals=short_sleep_days,
        )
    return None


def _check_illness(anomaly: Anomaly, context: dict) -> Optional[Investigation]:
    """Check if anomaly fits illness onset pattern."""
    sorted_dates = sorted(context.keys(), reverse=True)
    if not sorted_dates:
        return None

    latest = context.get(sorted_dates[0], {})
    chain = []
    signals = 0

    spo2 = latest.get("spo2")
    if spo2 is not None and spo2 < 94:
        chain.append(CauseLink(
            metric="spo2",
            observation=f"SpO2 low at {spo2:.1f}%",
            days_prior=0,
            value=spo2,
        ))
        signals += 1

    rhr = latest.get("rhr")
    if rhr is not None:
        # Check if RHR elevated vs 2 days ago
        for prev_date in sorted_dates[1:4]:
            prev_rhr = context.get(prev_date, {}).get("rhr")
            if prev_rhr is not None and rhr > prev_rhr * 1.05:
                chain.append(CauseLink(
                    metric="rhr",
                    observation=f"RHR elevated ({rhr:.0f} vs {prev_rhr:.0f} bpm)",
                    days_prior=0,
                    value=rhr,
                ))
                signals += 1
                break

    hrv = latest.get("hrv")
    if hrv is not None:
        for prev_date in sorted_dates[1:4]:
            prev_hrv = context.get(prev_date, {}).get("hrv")
            if prev_hrv is not None and hrv < prev_hrv * 0.8:
                chain.append(CauseLink(
                    metric="hrv",
                    observation=f"HRV dropped ({hrv:.1f} vs {prev_hrv:.1f}ms)",
                    days_prior=0,
                    value=hrv,
                ))
                signals += 1
                break

    sleep = latest.get("sleep_duration")
    if sleep is not None and sleep >= 6.5:
        chain.append(CauseLink(
            metric="sleep_duration",
            observation=f"Sleep was adequate ({sleep:.1f}h) — ruling out sleep debt",
            days_prior=0,
            value=sleep,
            context="rules out sleep debt",
        ))

    if signals >= 2:
        return Investigation(
            anomaly=anomaly,
            hypothesis="illness_onset",
            confidence="high" if signals >= 3 else "medium",
            cause_chain=chain,
            recommendation=HYPOTHESES["illness_onset"]["recommendation"],
            supporting_signals=signals,
        )
    return None


def _check_stress(anomaly: Anomaly, context: dict) -> Optional[Investigation]:
    """Check if anomaly fits stress response pattern."""
    if anomaly.metric not in ("hrv", "recovery"):
        return None

    sorted_dates = sorted(context.keys(), reverse=True)
    if not sorted_dates:
        return None

    latest = context.get(sorted_dates[0], {})
    chain = []

    hrv = latest.get("hrv")
    sleep = latest.get("sleep_duration")
    strain = latest.get("strain")

    if hrv is None:
        return None

    # HRV suppressed but sleep OK and strain not extreme
    if sleep is not None and sleep >= 7.0:
        chain.append(CauseLink(
            metric="sleep_duration",
            observation=f"Sleep adequate ({sleep:.1f}h)",
            days_prior=0,
            value=sleep,
        ))
    else:
        return None

    if strain is not None and strain < 14:
        chain.append(CauseLink(
            metric="strain",
            observation=f"Strain normal ({strain:.1f})",
            days_prior=0,
            value=strain,
        ))
    elif strain is None:
        chain.append(CauseLink(
            metric="strain",
            observation="No strain data (likely rest day)",
            days_prior=0,
        ))
    else:
        return None  # High strain — not stress pattern

    chain.append(CauseLink(
        metric="hrv",
        observation=f"HRV suppressed ({hrv:.1f}ms)",
        days_prior=0,
        value=hrv,
    ))

    return Investigation(
        anomaly=anomaly,
        hypothesis="stress_response",
        confidence="medium",
        cause_chain=chain,
        recommendation=HYPOTHESES["stress_response"]["recommendation"],
        supporting_signals=len(chain),
    )


def investigate_anomaly(anomaly: Anomaly, context: dict) -> Optional[Investigation]:
    """Try all hypothesis checkers, return best match."""
    checkers = [
        _check_illness,
        _check_overtraining,
        _check_sleep_debt,
        _check_stress,
    ]

    candidates = []
    for checker in checkers:
        result = checker(anomaly, context)
        if result:
            candidates.append(result)

    if not candidates:
        return None

    # Pick highest confidence
    conf_order = {"high": 3, "medium": 2, "low": 1}
    return max(candidates, key=lambda x: (conf_order.get(x.confidence, 0), x.supporting_signals))


async def investigate_all(anomalies: list[Anomaly]) -> list[Investigation]:
    """Run investigations for all anomalies."""
    if not anomalies:
        return []

    recovery_records = await _fetch_recovery_data(14)
    sleep_records = await _fetch_sleep_data(14)
    cycle_records = await _fetch_cycle_data(14)

    context = _get_recent_context(recovery_records, sleep_records, cycle_records)

    investigations = []
    for anomaly in anomalies:
        inv = investigate_anomaly(anomaly, context)
        if inv:
            investigations.append(inv)

    return investigations
