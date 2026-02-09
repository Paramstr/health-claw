"""Tests for analysis/strain.py"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from analysis.strain import (
    _extract_strain,
    compute_acute_chronic,
    compute_strain_recovery_balance,
    detect_overtraining,
    compute_workout_distribution,
    analyze_strain,
)


def _make_cycle(days_ago=0, strain=12.0):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "id": 2000 + days_ago,
        "user_id": 32850214,
        "created_at": now.isoformat(),
        "score_state": "SCORED",
        "score": {"strain": strain, "kilojoule": 8000, "average_heart_rate": 75, "max_heart_rate": 160},
    }


def _make_recovery(days_ago=0, score=70.0):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "cycle_id": 1000 + days_ago,
        "user_id": 32850214,
        "created_at": now.isoformat(),
        "score_state": "SCORED",
        "score": {"recovery_score": score, "resting_heart_rate": 60.0, "hrv_rmssd_milli": 65.0, "spo2_percentage": 95.0},
    }


def _make_workout(days_ago=0, strain=8.0, sport="activity", duration_min=45):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    start = now - timedelta(minutes=duration_min)
    return {
        "id": f"workout-{days_ago}",
        "user_id": 32850214,
        "created_at": now.isoformat(),
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": now.isoformat().replace("+00:00", "Z"),
        "sport_name": sport,
        "score_state": "SCORED",
        "score": {"strain": strain, "average_heart_rate": 120, "max_heart_rate": 160, "kilojoule": 500},
    }


class TestExtractStrain:
    def test_normal(self):
        assert _extract_strain(_make_cycle(strain=16.5)) == pytest.approx(16.5)

    def test_missing(self):
        assert _extract_strain({}) is None


class TestAcuteChronic:
    def test_normal(self):
        cycles = [_make_cycle(days_ago=i, strain=12.0) for i in range(15)]
        ac = compute_acute_chronic(cycles)
        assert ac is not None
        assert ac.ratio == pytest.approx(1.0, rel=0.1)
        assert ac.risk_level == "moderate"

    def test_high_acute(self):
        # Recent 7d much higher than 30d average
        cycles = [_make_cycle(days_ago=i, strain=18.0 if i < 7 else 8.0) for i in range(20)]
        ac = compute_acute_chronic(cycles)
        assert ac is not None
        assert ac.ratio > 1.3
        assert ac.risk_level == "high"

    def test_too_few(self):
        cycles = [_make_cycle(days_ago=i) for i in range(3)]
        assert compute_acute_chronic(cycles) is None


class TestStrainRecoveryBalance:
    def test_balanced(self):
        cycles = [_make_cycle(days_ago=i, strain=10.0) for i in range(7)]
        recovery = [_make_recovery(days_ago=i, score=70.0) for i in range(7)]
        balance = compute_strain_recovery_balance(cycles, recovery)
        assert balance is not None
        assert balance.ratio_trend in ("balanced", "recovery_dominant")

    def test_strain_dominant(self):
        cycles = [_make_cycle(days_ago=i, strain=18.0) for i in range(7)]
        recovery = [_make_recovery(days_ago=i, score=40.0) for i in range(7)]
        balance = compute_strain_recovery_balance(cycles, recovery)
        assert balance is not None
        assert balance.ratio_trend == "strain_dominant"

    def test_too_few(self):
        cycles = [_make_cycle(days_ago=i) for i in range(2)]
        recovery = [_make_recovery(days_ago=i) for i in range(2)]
        assert compute_strain_recovery_balance(cycles, recovery) is None


class TestOvertraining:
    def test_not_detected_stable(self):
        cycles = [_make_cycle(days_ago=i, strain=10.0) for i in range(14)]
        recovery = [_make_recovery(days_ago=i, score=70.0) for i in range(14)]
        ot = detect_overtraining(cycles, recovery)
        assert ot is not None
        assert ot.detected is False

    def test_detected(self):
        # Strain increasing, recovery decreasing
        cycles = [_make_cycle(days_ago=i, strain=8.0 + (14 - i) * 0.5) for i in range(14)]
        recovery = [_make_recovery(days_ago=i, score=80.0 - (14 - i) * 2.0) for i in range(14)]
        ot = detect_overtraining(cycles, recovery)
        assert ot is not None
        assert ot.detected is True

    def test_too_few(self):
        cycles = [_make_cycle(days_ago=i) for i in range(3)]
        recovery = [_make_recovery(days_ago=i) for i in range(3)]
        assert detect_overtraining(cycles, recovery) is None


class TestWorkoutDistribution:
    def test_normal(self):
        workouts = [
            _make_workout(days_ago=i, strain=6 + i * 2, sport="activity")
            for i in range(5)
        ]
        dist = compute_workout_distribution(workouts)
        assert dist is not None
        assert dist.total_workouts == 5
        assert dist.avg_strain_per_workout > 0
        assert dist.avg_duration_minutes > 0
        assert "activity" in dist.sport_breakdown

    def test_empty(self):
        assert compute_workout_distribution([]) is None

    def test_intensity_buckets(self):
        workouts = [
            _make_workout(strain=5.0),   # low
            _make_workout(strain=10.0),  # moderate
            _make_workout(strain=16.0),  # high
        ]
        dist = compute_workout_distribution(workouts)
        assert dist.low_count == 1
        assert dist.moderate_count == 1
        assert dist.high_intensity_count == 1


@pytest.mark.asyncio
async def test_analyze_strain_full():
    cycles = [_make_cycle(days_ago=i, strain=10 + i * 0.5) for i in range(14)]
    recovery = [_make_recovery(days_ago=i, score=65 + i) for i in range(14)]
    workouts = [_make_workout(days_ago=i, strain=8 + i) for i in range(8)]

    async def mock_fetch_cycle(days):
        return cycles

    async def mock_fetch_recovery(days):
        return recovery

    async def mock_fetch_workout(days):
        return workouts

    with patch("analysis.strain._fetch_cycle_data", side_effect=mock_fetch_cycle), \
         patch("analysis.strain._fetch_recovery_data", side_effect=mock_fetch_recovery), \
         patch("analysis.strain._fetch_workout_data", side_effect=mock_fetch_workout):
        report = await analyze_strain(session=None)

    assert report is not None
    assert report.latest_day_strain is not None
    assert report.acute_chronic is not None
    assert report.balance is not None
    assert report.overtraining is not None
    assert report.workout_dist is not None


@pytest.mark.asyncio
async def test_analyze_strain_empty():
    async def empty(days):
        return []

    with patch("analysis.strain._fetch_cycle_data", side_effect=empty), \
         patch("analysis.strain._fetch_recovery_data", side_effect=empty), \
         patch("analysis.strain._fetch_workout_data", side_effect=empty):
        report = await analyze_strain(session=None)

    assert report is not None
    assert report.latest_day_strain is None
