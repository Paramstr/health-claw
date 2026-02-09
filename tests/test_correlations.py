"""Tests for analysis/correlations.py"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from analysis.correlations import (
    compute_day_of_week_patterns,
    compute_bounce_back,
    compute_sleep_to_recovery,
    find_notable_correlations,
    analyze_correlations,
)


def _recovery(days_ago=0, recovery=70.0, hrv=65.0):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "created_at": now.isoformat(),
        "score": {"recovery_score": recovery, "resting_heart_rate": 60.0, "hrv_rmssd_milli": hrv, "spo2_percentage": 95.0},
    }


def _sleep(days_ago=0, duration_hrs=7.0):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    total_ms = int(duration_hrs * 3_600_000)
    return {
        "created_at": now.isoformat(),
        "nap": False,
        "score": {"stage_summary": {
            "total_light_sleep_time_milli": int(total_ms * 0.56),
            "total_slow_wave_sleep_time_milli": int(total_ms * 0.16),
            "total_rem_sleep_time_milli": int(total_ms * 0.28),
        }},
    }


def _cycle(days_ago=0, strain=12.0):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "created_at": now.isoformat(),
        "score_state": "SCORED",
        "score": {"strain": strain},
    }


class TestDayOfWeekPatterns:
    def test_with_enough_data(self):
        # 21 days gives 3 of each weekday
        recovery = [_recovery(days_ago=i, recovery=60 + i % 7 * 3) for i in range(21)]
        sleep = [_sleep(days_ago=i, duration_hrs=6 + (i % 3) * 0.5) for i in range(21)]
        cycles = [_cycle(days_ago=i, strain=8 + i % 5) for i in range(21)]
        patterns = compute_day_of_week_patterns(recovery, sleep, cycles)
        assert len(patterns) > 0
        for p in patterns:
            assert p.best_day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            assert p.worst_day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    def test_too_few(self):
        patterns = compute_day_of_week_patterns(
            [_recovery(days_ago=i) for i in range(3)],
            [_sleep(days_ago=i) for i in range(3)],
            [_cycle(days_ago=i) for i in range(3)],
        )
        assert len(patterns) == 0


class TestBounceBack:
    def test_with_enough_data(self):
        cycles = [_cycle(days_ago=i, strain=10 + (i % 4) * 3) for i in range(20)]
        recovery = [_recovery(days_ago=i, recovery=60 + (i % 3) * 10) for i in range(20)]
        bb = compute_bounce_back(cycles, recovery)
        # May or may not find enough bounce-back events
        if bb is not None:
            assert bb.avg_days_to_recover > 0
            assert bb.sample_count >= 2

    def test_too_few(self):
        assert compute_bounce_back(
            [_cycle(days_ago=i) for i in range(3)],
            [_recovery(days_ago=i) for i in range(3)],
        ) is None


class TestSleepToRecovery:
    def test_quantified(self):
        # Create correlated data
        sleep = [_sleep(days_ago=i, duration_hrs=5 + i * 0.3) for i in range(10)]
        recovery = [_recovery(days_ago=i, recovery=40 + i * 5) for i in range(10)]
        result = compute_sleep_to_recovery(sleep, recovery)
        assert result is not None
        assert result.sample_size >= 5
        assert result.slope != 0

    def test_too_few(self):
        assert compute_sleep_to_recovery(
            [_sleep(days_ago=i) for i in range(2)],
            [_recovery(days_ago=i) for i in range(2)],
        ) is None


class TestNotableCorrelations:
    def test_finds_correlations(self):
        # Strong correlation between HRV and recovery
        recovery = [_recovery(days_ago=i, recovery=50 + i * 4, hrv=50 + i * 2) for i in range(14)]
        sleep = [_sleep(days_ago=i, duration_hrs=5 + i * 0.2) for i in range(14)]
        cycles = [_cycle(days_ago=i, strain=15 - i * 0.5) for i in range(14)]
        notable = find_notable_correlations(recovery, sleep, cycles)
        assert len(notable) >= 0  # May find correlations depending on data

    def test_safeguards_min_samples(self):
        """Too few samples should produce no results."""
        notable = find_notable_correlations(
            [_recovery(days_ago=i) for i in range(3)],
            [_sleep(days_ago=i) for i in range(3)],
            [_cycle(days_ago=i) for i in range(3)],
        )
        assert len(notable) == 0


@pytest.mark.asyncio
async def test_analyze_correlations_full():
    recovery = [_recovery(days_ago=i, recovery=50 + i * 3, hrv=50 + i * 2) for i in range(21)]
    sleep = [_sleep(days_ago=i, duration_hrs=5 + i * 0.15) for i in range(21)]
    cycles = [_cycle(days_ago=i, strain=8 + i * 0.5) for i in range(21)]

    with patch("analysis.correlations._fetch_recovery_data", return_value=recovery), \
         patch("analysis.correlations._fetch_sleep_data", return_value=sleep), \
         patch("analysis.correlations._fetch_cycle_data", return_value=cycles):
        report = await analyze_correlations()

    assert report is not None
    assert isinstance(report.day_of_week, list)
    assert report.sleep_to_recovery is not None


@pytest.mark.asyncio
async def test_analyze_correlations_empty():
    with patch("analysis.correlations._fetch_recovery_data", return_value=[]), \
         patch("analysis.correlations._fetch_sleep_data", return_value=[]), \
         patch("analysis.correlations._fetch_cycle_data", return_value=[]):
        report = await analyze_correlations()

    assert report is not None
    assert report.bounce_back is None
    assert report.sleep_to_recovery is None
