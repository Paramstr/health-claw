"""Tests for analysis/investigations.py"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from analysis.anomalies import Anomaly
from analysis.baselines import BaselineResult
from analysis.investigations import (
    investigate_low_recovery,
    investigate_spo2_drop,
    investigate_generic,
    investigate_anomalies,
)


def _bl(metric, mean, std):
    now = datetime.now(timezone.utc)
    return BaselineResult(metric=metric, window="30d", mean=mean, std=std,
                          sample_count=20, window_start=now - timedelta(days=30), window_end=now)


def _anomaly(metric="recovery", severity="watch", z=-2.0, value=45.0, mean=70.0, std=10.0):
    return Anomaly(metric=metric, severity=severity, z_score=z, current_value=value,
                   baseline_mean=mean, baseline_std=std, direction="below",
                   sustained_days=1, description="test", fingerprint="abc123")


def _recovery(days_ago=0, hrv=65.0, rhr=60.0, recovery=70.0):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "created_at": now.isoformat(),
        "score": {"recovery_score": recovery, "resting_heart_rate": rhr,
                  "hrv_rmssd_milli": hrv, "spo2_percentage": 95.0},
    }


def _sleep(days_ago=0, duration_hrs=7.0, deep_pct=0.16, rem_pct=0.28):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    total_ms = int(duration_hrs * 3_600_000)
    return {
        "created_at": now.isoformat(),
        "nap": False,
        "score": {"stage_summary": {
            "total_in_bed_time_milli": total_ms + 2_000_000,
            "total_awake_time_milli": 2_000_000,
            "total_light_sleep_time_milli": int(total_ms * (1 - deep_pct - rem_pct)),
            "total_slow_wave_sleep_time_milli": int(total_ms * deep_pct),
            "total_rem_sleep_time_milli": int(total_ms * rem_pct),
        }},
    }


def _cycle(days_ago=0, strain=12.0):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {"created_at": now.isoformat(), "score_state": "SCORED", "score": {"strain": strain}}


class TestInvestigateLowRecovery:
    def test_short_sleep(self):
        anomaly = _anomaly()
        baselines = {"sleep_duration": {"30d": _bl("sleep_duration", 7.5, 0.5)}}
        inv = investigate_low_recovery(
            anomaly, [_recovery(recovery=45)], [_sleep(duration_hrs=5.0)], [_cycle()], baselines
        )
        assert any("sleep" in c.factor.lower() for c in inv.cause_chain)
        assert inv.confidence in ("high", "medium", "low")

    def test_high_strain(self):
        anomaly = _anomaly()
        baselines = {
            "strain": {"30d": _bl("strain", 10.0, 2.0)},
            "sleep_duration": {"30d": _bl("sleep_duration", 7.5, 0.5)},
        }
        inv = investigate_low_recovery(
            anomaly, [_recovery()], [_sleep()], [_cycle(strain=18.0)], baselines
        )
        assert any("strain" in c.factor.lower() for c in inv.cause_chain)

    def test_hrv_suppressed(self):
        anomaly = _anomaly()
        baselines = {
            "hrv": {"30d": _bl("hrv", 65.0, 5.0)},
            "sleep_duration": {"30d": _bl("sleep_duration", 7.5, 0.5)},
        }
        inv = investigate_low_recovery(
            anomaly, [_recovery(hrv=48.0)], [_sleep()], [_cycle()], baselines
        )
        assert any("hrv" in c.factor.lower() for c in inv.cause_chain)

    def test_no_data(self):
        anomaly = _anomaly()
        inv = investigate_low_recovery(anomaly, [], [], [], {})
        assert inv.confidence == "low"


class TestInvestigateSpo2:
    def test_produces_chain(self):
        anomaly = _anomaly(metric="spo2", value=91.0)
        inv = investigate_spo2_drop(anomaly)
        assert len(inv.cause_chain) >= 1
        assert "91.0" in inv.summary


class TestInvestigateGeneric:
    def test_produces_result(self):
        anomaly = _anomaly(metric="rhr")
        inv = investigate_generic(anomaly)
        assert inv.anomaly == anomaly
        assert inv.confidence == "low"


@pytest.mark.asyncio
async def test_investigate_anomalies_full():
    anomalies = [
        _anomaly(metric="recovery", severity="watch", z=-2.0, value=45.0),
        _anomaly(metric="spo2", severity="alert", z=None, value=91.0),
    ]

    with patch("analysis.investigations._fetch_recovery_data", return_value=[_recovery(recovery=45, hrv=50)]), \
         patch("analysis.investigations._fetch_sleep_data", return_value=[_sleep(duration_hrs=5.5)]), \
         patch("analysis.investigations._fetch_cycle_data", return_value=[_cycle(strain=16)]):
        results = await investigate_anomalies(anomalies, baselines={
            "sleep_duration": {"30d": _bl("sleep_duration", 7.5, 0.5)},
            "strain": {"30d": _bl("strain", 10, 2)},
            "hrv": {"30d": _bl("hrv", 65, 5)},
        })

    assert len(results) == 2
    assert results[0].anomaly.metric == "recovery"
    assert len(results[0].cause_chain) >= 1
    assert results[1].anomaly.metric == "spo2"


@pytest.mark.asyncio
async def test_investigate_empty():
    with patch("analysis.investigations._fetch_recovery_data", return_value=[]), \
         patch("analysis.investigations._fetch_sleep_data", return_value=[]), \
         patch("analysis.investigations._fetch_cycle_data", return_value=[]):
        results = await investigate_anomalies([])

    assert results == []
