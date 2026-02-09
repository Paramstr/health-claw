"""Tests for analysis/sleep.py"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from analysis.sleep import (
    _extract_stages,
    _extract_bedtime_waketime,
    compute_sleep_trend,
    compute_stage_analysis,
    compute_consistency,
    compute_sleep_debt,
    analyze_sleep,
    SleepStages,
)


# ─── Helpers ──────────────────────────────────────────────────────

def _make_sleep_record(
    days_ago: int = 0,
    light_ms: int = 14_000_000,
    deep_ms: int = 4_000_000,
    rem_ms: int = 7_000_000,
    awake_ms: int = 2_000_000,
    nap: bool = False,
    bedtime_hour: float = 22.0,  # local hour
    sleep_hours: float = 7.5,
    tz_offset: str = "+11:00",
    baseline_need_ms: int = 28_000_000,
) -> dict:
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    total_in_bed = light_ms + deep_ms + rem_ms + awake_ms
    start = now - timedelta(hours=sleep_hours)
    return {
        "id": f"sleep-{days_ago}",
        "user_id": 32850214,
        "created_at": now.isoformat(),
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": now.isoformat().replace("+00:00", "Z"),
        "timezone_offset": tz_offset,
        "nap": nap,
        "score_state": "SCORED",
        "score": {
            "stage_summary": {
                "total_in_bed_time_milli": total_in_bed,
                "total_awake_time_milli": awake_ms,
                "total_no_data_time_milli": 0,
                "total_light_sleep_time_milli": light_ms,
                "total_slow_wave_sleep_time_milli": deep_ms,
                "total_rem_sleep_time_milli": rem_ms,
                "sleep_cycle_count": 5,
                "disturbance_count": 10,
            },
            "sleep_needed": {
                "baseline_milli": baseline_need_ms,
                "need_from_sleep_debt_milli": 3_000_000,
                "need_from_recent_strain_milli": 1_000_000,
                "need_from_recent_nap_milli": 0,
            },
            "sleep_efficiency_percentage": 92.0,
            "sleep_performance_percentage": 80.0,
            "respiratory_rate": 13.0,
        },
    }


def _make_recovery_record(days_ago: int = 0, recovery_score: float = 70.0) -> dict:
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "cycle_id": 1000 + days_ago,
        "user_id": 32850214,
        "created_at": now.isoformat(),
        "score_state": "SCORED",
        "score": {
            "recovery_score": recovery_score,
            "resting_heart_rate": 60.0,
            "hrv_rmssd_milli": 65.0,
            "spo2_percentage": 95.0,
        },
    }


# ─── Unit tests ───────────────────────────────────────────────────

class TestExtractStages:
    def test_normal(self):
        record = _make_sleep_record()
        stages = _extract_stages(record)
        assert stages is not None
        total = 14_000_000 + 4_000_000 + 7_000_000
        assert stages.light_pct == pytest.approx(14_000_000 / total * 100)
        assert stages.deep_pct == pytest.approx(4_000_000 / total * 100)
        assert stages.rem_pct == pytest.approx(7_000_000 / total * 100)

    def test_missing_score(self):
        assert _extract_stages({}) is None

    def test_zero_sleep(self):
        record = _make_sleep_record(light_ms=0, deep_ms=0, rem_ms=0)
        assert _extract_stages(record) is None


class TestExtractBedtimeWaketime:
    def test_normal(self):
        record = _make_sleep_record(tz_offset="+00:00")
        bed, wake = _extract_bedtime_waketime(record)
        assert bed is not None
        assert wake is not None

    def test_missing_times(self):
        bed, wake = _extract_bedtime_waketime({})
        assert bed is None
        assert wake is None


class TestComputeSleepTrend:
    def test_stable(self):
        records = [_make_sleep_record(days_ago=i) for i in range(7)]
        trend = compute_sleep_trend(records)
        assert trend is not None
        assert trend.direction == "stable"
        assert trend.duration_7d_avg_hrs > 0

    def test_improving(self):
        # Earlier records have less sleep, recent have more
        records = [
            _make_sleep_record(
                days_ago=i,
                light_ms=14_000_000 - i * 1_000_000,
                deep_ms=4_000_000 - i * 500_000,
                rem_ms=7_000_000 - i * 500_000,
            )
            for i in range(7)
        ]
        trend = compute_sleep_trend(records)
        assert trend is not None
        # Records reversed for chronological, so most recent (i=0) has most sleep
        assert trend.direction == "improving"

    def test_too_few(self):
        records = [_make_sleep_record(days_ago=i) for i in range(2)]
        assert compute_sleep_trend(records) is None

    def test_excludes_naps(self):
        records = [_make_sleep_record(days_ago=i) for i in range(5)]
        records.insert(1, _make_sleep_record(days_ago=1, nap=True))
        trend = compute_sleep_trend(records)
        assert trend is not None


class TestComputeStageAnalysis:
    def test_normal(self):
        records = [_make_sleep_record(days_ago=i) for i in range(5)]
        analysis = compute_stage_analysis(records)
        assert analysis is not None
        assert analysis.current_rem_pct > 0
        assert analysis.baseline_rem_pct is not None

    def test_low_rem_flagged(self):
        # Baseline records with some variation in REM
        records = [
            _make_sleep_record(days_ago=i, rem_ms=7_000_000 + i * 200_000, light_ms=14_000_000 - i * 200_000)
            for i in range(7)
        ]
        # Make current record have very low REM
        records[0] = _make_sleep_record(rem_ms=1_000_000, light_ms=18_000_000)
        analysis = compute_stage_analysis(records)
        assert analysis is not None
        assert analysis.rem_flag == "low"

    def test_too_few(self):
        records = [_make_sleep_record()]
        assert compute_stage_analysis(records) is None


class TestComputeConsistency:
    def test_consistent(self):
        records = [_make_sleep_record(days_ago=i) for i in range(7)]
        consistency = compute_consistency(records)
        assert consistency is not None
        assert consistency.consistency_score > 50

    def test_with_recovery(self):
        sleep_records = [_make_sleep_record(days_ago=i) for i in range(5)]
        recovery_records = [_make_recovery_record(days_ago=i, recovery_score=70 + i) for i in range(5)]
        consistency = compute_consistency(sleep_records, recovery_records)
        assert consistency is not None

    def test_too_few(self):
        records = [_make_sleep_record(days_ago=i) for i in range(2)]
        assert compute_consistency(records) is None


class TestComputeSleepDebt:
    def test_normal(self):
        records = [_make_sleep_record(days_ago=i) for i in range(7)]
        debt = compute_sleep_debt(records)
        assert debt is not None
        assert debt.baseline_need_hrs > 0
        assert debt.days_analyzed == 7

    def test_uses_whoop_baseline(self):
        records = [_make_sleep_record(days_ago=i, baseline_need_ms=30_000_000) for i in range(5)]
        debt = compute_sleep_debt(records)
        assert debt is not None
        # 30_000_000 ms = 8.33 hrs
        assert debt.baseline_need_hrs == pytest.approx(30_000_000 / 3_600_000, rel=0.01)

    def test_too_few(self):
        records = [_make_sleep_record(days_ago=i) for i in range(2)]
        assert compute_sleep_debt(records) is None


# ─── Integration test ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analyze_sleep_full():
    """Full analysis with mocked API calls."""
    sleep_records = [_make_sleep_record(days_ago=i) for i in range(10)]
    recovery_records = [_make_recovery_record(days_ago=i) for i in range(10)]

    with patch("analysis.sleep._fetch_sleep_data", return_value=sleep_records), \
         patch("analysis.sleep._fetch_recovery_data", return_value=recovery_records):
        report = await analyze_sleep(session=None)

    assert report is not None
    assert report.last_sleep_duration_hrs is not None
    assert report.last_sleep_duration_hrs > 0
    assert report.efficiency_pct is not None
    assert report.trend is not None
    assert report.stages is not None
    assert report.consistency is not None
    assert report.debt is not None


@pytest.mark.asyncio
async def test_analyze_sleep_empty():
    """Analysis with no data should not crash."""
    with patch("analysis.sleep._fetch_sleep_data", return_value=[]), \
         patch("analysis.sleep._fetch_recovery_data", return_value=[]):
        report = await analyze_sleep(session=None)

    assert report is not None
    assert report.last_sleep_duration_hrs is None
    assert report.trend is None
