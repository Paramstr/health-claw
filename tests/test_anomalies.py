"""Tests for analysis/anomalies.py"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from analysis.anomalies import (
    Anomaly,
    _compute_fingerprint,
    _severity_from_z,
    detect_z_score_anomalies,
    detect_spo2_drop,
    detect_rhr_trend,
    detect_hrv_suppression,
    detect_all_anomalies,
)
from analysis.baselines import BaselineResult


def _bl(metric="hrv", mean=65.0, std=5.0):
    now = datetime.now(timezone.utc)
    return BaselineResult(metric=metric, window="30d", mean=mean, std=std,
                          sample_count=20, window_start=now - timedelta(days=30), window_end=now)


def _recovery(days_ago=0, hrv=65.0, rhr=60.0, spo2=95.0, recovery=70.0):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "created_at": now.isoformat(),
        "score": {
            "hrv_rmssd_milli": hrv,
            "resting_heart_rate": rhr,
            "spo2_percentage": spo2,
            "recovery_score": recovery,
        },
    }


def _sleep(days_ago=0, duration_hrs=7.0):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    total_ms = int(duration_hrs * 3_600_000)
    return {
        "created_at": now.isoformat(),
        "nap": False,
        "score": {
            "stage_summary": {
                "total_light_sleep_time_milli": int(total_ms * 0.55),
                "total_slow_wave_sleep_time_milli": int(total_ms * 0.16),
                "total_rem_sleep_time_milli": int(total_ms * 0.29),
            }
        },
    }


class TestFingerprint:
    def test_deterministic(self):
        fp1 = _compute_fingerprint("hrv", "alert", "below")
        fp2 = _compute_fingerprint("hrv", "alert", "below")
        assert fp1 == fp2

    def test_different(self):
        fp1 = _compute_fingerprint("hrv", "alert", "below")
        fp2 = _compute_fingerprint("rhr", "alert", "above")
        assert fp1 != fp2


class TestSeverity:
    def test_info(self):
        assert _severity_from_z(1.7) == "info"

    def test_watch(self):
        assert _severity_from_z(2.2) == "watch"

    def test_watch_sustained(self):
        assert _severity_from_z(1.6, sustained_days=3) == "watch"

    def test_alert(self):
        assert _severity_from_z(2.8) == "alert"

    def test_alert_multi(self):
        assert _severity_from_z(1.6, sustained_days=2, multi_signal=True) == "alert"

    def test_none(self):
        assert _severity_from_z(1.0) == "none"


class TestZScoreAnomalies:
    def test_detects_anomaly(self):
        bl = _bl("hrv", mean=65.0, std=5.0)
        now = datetime.now(timezone.utc)
        values = [(now, 50.0)]  # z = -3.0
        anomalies = detect_z_score_anomalies("hrv", values, bl)
        assert len(anomalies) == 1
        assert anomalies[0].severity == "alert"
        assert anomalies[0].direction == "below"

    def test_no_anomaly(self):
        bl = _bl("hrv", mean=65.0, std=5.0)
        now = datetime.now(timezone.utc)
        values = [(now, 64.0)]  # z = -0.2
        anomalies = detect_z_score_anomalies("hrv", values, bl)
        assert len(anomalies) == 0

    def test_sustained(self):
        bl = _bl("hrv", mean=65.0, std=5.0)
        now = datetime.now(timezone.utc)
        values = [
            (now, 55.0),  # z = -2.0
            (now - timedelta(days=1), 54.0),  # z = -2.2
            (now - timedelta(days=2), 56.0),  # z = -1.8
        ]
        anomalies = detect_z_score_anomalies("hrv", values, bl)
        assert len(anomalies) == 1
        assert anomalies[0].sustained_days == 3

    def test_no_baseline(self):
        assert detect_z_score_anomalies("hrv", [(datetime.now(timezone.utc), 50.0)], None) == []

    def test_zero_std(self):
        bl = _bl("hrv", mean=65.0, std=0.0)
        assert detect_z_score_anomalies("hrv", [(datetime.now(timezone.utc), 50.0)], bl) == []


class TestSpo2Drop:
    def test_detects_low(self):
        records = [_recovery(spo2=91.0)]
        anomalies = detect_spo2_drop(records)
        assert len(anomalies) == 1
        assert anomalies[0].severity == "alert"

    def test_moderate_low(self):
        records = [_recovery(spo2=93.0)]
        anomalies = detect_spo2_drop(records)
        assert len(anomalies) == 1
        assert anomalies[0].severity == "watch"

    def test_normal(self):
        records = [_recovery(spo2=96.0)]
        assert detect_spo2_drop(records) == []


class TestRhrTrend:
    def test_detects_rising(self):
        # Most recent is highest (rising trend)
        records = [_recovery(days_ago=i, rhr=65.0 - i) for i in range(5)]
        anomalies = detect_rhr_trend(records)
        assert len(anomalies) == 1
        assert anomalies[0].sustained_days >= 3

    def test_stable(self):
        records = [_recovery(days_ago=i, rhr=60.0) for i in range(5)]
        assert detect_rhr_trend(records) == []


class TestHrvSuppression:
    def test_detected(self):
        recovery = [_recovery(hrv=50.0)]  # Low HRV
        sleep = [_sleep(duration_hrs=8.0)]  # Good sleep
        baselines = {
            "hrv": {"30d": _bl("hrv", mean=65.0, std=5.0)},
            "sleep_duration": {"30d": _bl("sleep_duration", mean=7.0, std=0.5)},
        }
        anomalies = detect_hrv_suppression(recovery, sleep, baselines)
        assert len(anomalies) == 1
        assert "stress" in anomalies[0].description.lower() or "illness" in anomalies[0].description.lower()

    def test_not_detected_low_sleep(self):
        """HRV low but sleep was also short — explained."""
        recovery = [_recovery(hrv=50.0)]
        sleep = [_sleep(duration_hrs=4.5)]
        baselines = {
            "hrv": {"30d": _bl("hrv", mean=65.0, std=5.0)},
            "sleep_duration": {"30d": _bl("sleep_duration", mean=7.0, std=0.5)},
        }
        anomalies = detect_hrv_suppression(recovery, sleep, baselines)
        assert len(anomalies) == 0

    def test_not_detected_normal_hrv(self):
        recovery = [_recovery(hrv=63.0)]
        sleep = [_sleep(duration_hrs=7.5)]
        baselines = {
            "hrv": {"30d": _bl("hrv", mean=65.0, std=5.0)},
            "sleep_duration": {"30d": _bl("sleep_duration", mean=7.0, std=0.5)},
        }
        assert detect_hrv_suppression(recovery, sleep, baselines) == []


@pytest.mark.asyncio
async def test_detect_all_anomalies():
    recovery = [_recovery(days_ago=i, hrv=50.0, spo2=91.0) for i in range(7)]
    sleep = [_sleep(days_ago=i) for i in range(7)]
    cycles = []

    async def mock_recovery(days):
        return recovery
    async def mock_sleep(days):
        return sleep
    async def mock_cycle(days):
        return cycles

    with patch("analysis.anomalies._fetch_recovery_data", side_effect=mock_recovery), \
         patch("analysis.anomalies._fetch_sleep_data", side_effect=mock_sleep), \
         patch("analysis.anomalies._fetch_cycle_data", side_effect=mock_cycle):
        anomalies = await detect_all_anomalies(session=None)

    # Should detect SpO2 drop at minimum
    assert any(a.metric == "spo2" for a in anomalies)
