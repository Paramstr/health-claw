"""Tests for analysis/recovery.py"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pytest

from analysis.recovery import (
    _extract_recovery_score,
    _extract_hrv,
    _extract_rhr,
    compute_recovery_trend,
    compute_recovery_prediction,
    compute_sleep_recovery_correlation,
    analyze_recovery,
)


def _make_recovery(days_ago=0, score=70.0, hrv=65.0, rhr=60.0):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "cycle_id": 1000 + days_ago,
        "user_id": 32850214,
        "created_at": now.isoformat(),
        "score_state": "SCORED",
        "score": {
            "recovery_score": score,
            "resting_heart_rate": rhr,
            "hrv_rmssd_milli": hrv,
            "spo2_percentage": 95.0,
        },
    }


def _make_sleep(days_ago=0, duration_hrs=7.0, efficiency=92.0, deep_pct_target=16.0):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    total_sleep_ms = int(duration_hrs * 3_600_000)
    deep_ms = int(total_sleep_ms * deep_pct_target / 100)
    rem_ms = int(total_sleep_ms * 0.28)
    light_ms = total_sleep_ms - deep_ms - rem_ms
    awake_ms = int(total_sleep_ms * (100 - efficiency) / efficiency)
    return {
        "id": f"sleep-{days_ago}",
        "user_id": 32850214,
        "created_at": now.isoformat(),
        "nap": False,
        "score_state": "SCORED",
        "score": {
            "stage_summary": {
                "total_in_bed_time_milli": total_sleep_ms + awake_ms,
                "total_awake_time_milli": awake_ms,
                "total_no_data_time_milli": 0,
                "total_light_sleep_time_milli": light_ms,
                "total_slow_wave_sleep_time_milli": deep_ms,
                "total_rem_sleep_time_milli": rem_ms,
                "sleep_cycle_count": 5,
                "disturbance_count": 10,
            },
            "sleep_efficiency_percentage": efficiency,
        },
    }


class TestExtractors:
    def test_recovery_score(self):
        assert _extract_recovery_score(_make_recovery(score=84.0)) == pytest.approx(84.0)

    def test_recovery_score_missing(self):
        assert _extract_recovery_score({}) is None

    def test_hrv(self):
        assert _extract_hrv(_make_recovery(hrv=68.2)) == pytest.approx(68.2)

    def test_rhr(self):
        assert _extract_rhr(_make_recovery(rhr=58.0)) == pytest.approx(58.0)


class TestRecoveryTrend:
    def test_stable(self):
        records = [_make_recovery(days_ago=i, score=70.0) for i in range(7)]
        trend = compute_recovery_trend(records)
        assert trend is not None
        assert trend.direction == "stable"

    def test_improving(self):
        # Most recent (i=0) has highest score
        records = [_make_recovery(days_ago=i, score=90 - i * 5) for i in range(7)]
        trend = compute_recovery_trend(records)
        assert trend is not None
        assert trend.direction == "improving"

    def test_declining(self):
        records = [_make_recovery(days_ago=i, score=40 + i * 5) for i in range(7)]
        trend = compute_recovery_trend(records)
        assert trend is not None
        assert trend.direction == "declining"

    def test_too_few(self):
        records = [_make_recovery(days_ago=i) for i in range(2)]
        assert compute_recovery_trend(records) is None


class TestRecoveryPrediction:
    def test_with_enough_data(self):
        # Create correlated data: more sleep -> higher recovery
        recovery = [_make_recovery(days_ago=i, score=50 + i * 3) for i in range(10)]
        sleep = [_make_sleep(days_ago=i, duration_hrs=5.0 + i * 0.3, efficiency=85 + i, deep_pct_target=12 + i) for i in range(10)]
        pred = compute_recovery_prediction(recovery, sleep)
        assert pred is not None
        assert pred.model_r_squared >= 0
        assert pred.actual_score == pytest.approx(50.0)
        assert len(pred.features_used) == 3

    def test_too_few(self):
        recovery = [_make_recovery(days_ago=i) for i in range(3)]
        sleep = [_make_sleep(days_ago=i) for i in range(3)]
        assert compute_recovery_prediction(recovery, sleep) is None


class TestSleepRecoveryCorrelation:
    def test_with_enough_data(self):
        recovery = [_make_recovery(days_ago=i, score=50 + i * 3) for i in range(8)]
        sleep = [_make_sleep(days_ago=i, duration_hrs=5.0 + i * 0.3) for i in range(8)]
        corr = compute_sleep_recovery_correlation(recovery, sleep)
        assert corr is not None
        assert corr.sample_size >= 4
        assert corr.duration_corr is not None

    def test_too_few(self):
        recovery = [_make_recovery(days_ago=i) for i in range(2)]
        sleep = [_make_sleep(days_ago=i) for i in range(2)]
        assert compute_sleep_recovery_correlation(recovery, sleep) is None


@pytest.mark.asyncio
async def test_analyze_recovery_full():
    recovery = [_make_recovery(days_ago=i, score=60 + i * 2) for i in range(14)]
    sleep = [_make_sleep(days_ago=i, duration_hrs=6.0 + i * 0.2) for i in range(14)]

    with patch("analysis.recovery._fetch_recovery_data", return_value=recovery), \
         patch("analysis.recovery._fetch_sleep_data", return_value=sleep):
        report = await analyze_recovery(session=None)

    assert report is not None
    assert report.latest_score == pytest.approx(60.0)
    assert report.trend is not None
    assert report.prediction is not None
    assert report.sleep_correlation is not None


@pytest.mark.asyncio
async def test_analyze_recovery_empty():
    with patch("analysis.recovery._fetch_recovery_data", return_value=[]), \
         patch("analysis.recovery._fetch_sleep_data", return_value=[]):
        report = await analyze_recovery(session=None)

    assert report is not None
    assert report.latest_score is None
    assert report.trend is None
