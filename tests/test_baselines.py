"""Tests for analysis/baselines.py — unit tests with mocked API calls."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import numpy as np
import pytest

from analysis.baselines import (
    _extract_metric_from_recovery,
    _extract_sleep_duration,
    _extract_strain,
    _record_date,
    compute_baseline_from_values,
    compute_baselines,
    BaselineResult,
)


# ─── Unit tests for extraction functions ───────────────────────────

class TestExtractMetricFromRecovery:
    def test_hrv(self):
        record = {"score": {"hrv_rmssd_milli": 68.2, "resting_heart_rate": 60.0, "recovery_score": 84.0, "spo2_percentage": 91.9}}
        assert _extract_metric_from_recovery(record, "hrv") == pytest.approx(68.2)

    def test_rhr(self):
        record = {"score": {"resting_heart_rate": 58.0}}
        assert _extract_metric_from_recovery(record, "rhr") == pytest.approx(58.0)

    def test_recovery(self):
        record = {"score": {"recovery_score": 39.0}}
        assert _extract_metric_from_recovery(record, "recovery") == pytest.approx(39.0)

    def test_spo2(self):
        record = {"score": {"spo2_percentage": 96.4}}
        assert _extract_metric_from_recovery(record, "spo2") == pytest.approx(96.4)

    def test_missing_score(self):
        assert _extract_metric_from_recovery({}, "hrv") is None

    def test_missing_field(self):
        record = {"score": {}}
        assert _extract_metric_from_recovery(record, "hrv") is None

    def test_none_value(self):
        record = {"score": {"hrv_rmssd_milli": None}}
        assert _extract_metric_from_recovery(record, "hrv") is None


class TestExtractSleepDuration:
    def test_normal(self):
        record = {
            "score": {
                "stage_summary": {
                    "total_light_sleep_time_milli": 15343779,
                    "total_slow_wave_sleep_time_milli": 3964782,
                    "total_rem_sleep_time_milli": 7657873,
                }
            }
        }
        expected_hours = (15343779 + 3964782 + 7657873) / 3_600_000
        assert _extract_sleep_duration(record) == pytest.approx(expected_hours, rel=1e-4)

    def test_missing_score(self):
        assert _extract_sleep_duration({}) is None

    def test_missing_stages(self):
        assert _extract_sleep_duration({"score": {}}) is None


class TestExtractStrain:
    def test_normal(self):
        record = {"score": {"strain": 16.467}}
        assert _extract_strain(record) == pytest.approx(16.467)

    def test_missing(self):
        assert _extract_strain({}) is None
        assert _extract_strain({"score": {}}) is None


class TestRecordDate:
    def test_parses_utc(self):
        d = _record_date({"created_at": "2026-02-08T20:05:27.587Z"})
        assert d.year == 2026
        assert d.month == 2
        assert d.day == 8

    def test_missing(self):
        assert _record_date({}) is None


class TestComputeBaselineFromValues:
    def test_normal(self):
        values = [60.0, 62.0, 58.0, 64.0, 66.0]
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=7)
        result = compute_baseline_from_values(values, "hrv", "7d", start, now)
        assert result is not None
        assert result.metric == "hrv"
        assert result.window == "7d"
        assert result.mean == pytest.approx(np.mean(values))
        assert result.std == pytest.approx(np.std(values, ddof=1))
        assert result.sample_count == 5

    def test_too_few(self):
        now = datetime.now(timezone.utc)
        result = compute_baseline_from_values([60.0], "hrv", "7d", now - timedelta(days=7), now)
        assert result is None

    def test_empty(self):
        now = datetime.now(timezone.utc)
        result = compute_baseline_from_values([], "hrv", "7d", now - timedelta(days=7), now)
        assert result is None


# ─── Integration test with mocked API + DB ─────────────────────────

def _make_recovery_records(n: int, base_hrv: float = 65.0) -> list[dict]:
    """Generate n fake recovery records."""
    records = []
    now = datetime.now(timezone.utc)
    for i in range(n):
        records.append({
            "cycle_id": 1000 + i,
            "user_id": 32850214,
            "created_at": (now - timedelta(days=i)).isoformat(),
            "score_state": "SCORED",
            "score": {
                "recovery_score": 70.0 + i,
                "resting_heart_rate": 58.0 + (i % 5),
                "hrv_rmssd_milli": base_hrv + (i * 2),
                "spo2_percentage": 95.0 + (i % 3),
            },
        })
    return records


def _make_sleep_records(n: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    records = []
    for i in range(n):
        records.append({
            "id": f"sleep-{i}",
            "user_id": 32850214,
            "created_at": (now - timedelta(days=i)).isoformat(),
            "nap": False,
            "score_state": "SCORED",
            "score": {
                "stage_summary": {
                    "total_light_sleep_time_milli": 14_000_000 + i * 100000,
                    "total_slow_wave_sleep_time_milli": 4_000_000,
                    "total_rem_sleep_time_milli": 7_000_000,
                }
            },
        })
    return records


def _make_cycle_records(n: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    records = []
    for i in range(n):
        records.append({
            "id": 2000 + i,
            "user_id": 32850214,
            "created_at": (now - timedelta(days=i)).isoformat(),
            "score_state": "SCORED",
            "score": {"strain": 10.0 + i},
        })
    return records


@pytest.mark.asyncio
async def test_compute_baselines_stores_results():
    """Test that compute_baselines produces correct results with mocked data."""
    recovery_records = _make_recovery_records(10)
    sleep_records = _make_sleep_records(10)
    cycle_records = _make_cycle_records(10)

    async def mock_list_records(user_id, kind, limit=25, start=None, end=None, next_token=None):
        mapping = {
            "recovery": recovery_records,
            "sleep": sleep_records,
            "cycle": cycle_records,
        }
        return {"records": mapping.get(kind, []), "next_token": None}

    # Mock session
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    added_objects = []
    mock_session.add = lambda obj: added_objects.append(obj)

    with patch("analysis.baselines.list_records", side_effect=mock_list_records):
        results = await compute_baselines(mock_session)

    # Should have results for 6 metrics × 2 windows = 12
    assert len(results) == 12
    metrics_found = {r.metric for r in results}
    assert metrics_found == {"hrv", "rhr", "sleep_duration", "strain", "recovery", "spo2"}

    # Check that DB objects were added
    assert len(added_objects) == 12

    # Verify a specific baseline
    hrv_7d = [r for r in results if r.metric == "hrv" and r.window == "7d"][0]
    assert hrv_7d.sample_count == 7
    assert hrv_7d.mean > 0
    assert hrv_7d.std > 0


@pytest.mark.asyncio
async def test_compute_baselines_sparse_data():
    """With only 3 records, 7d should still work but values reflect small sample."""
    recovery_records = _make_recovery_records(3)
    sleep_records = _make_sleep_records(3)
    cycle_records = _make_cycle_records(3)

    async def mock_list_records(user_id, kind, limit=25, start=None, end=None, next_token=None):
        mapping = {"recovery": recovery_records, "sleep": sleep_records, "cycle": cycle_records}
        return {"records": mapping.get(kind, []), "next_token": None}

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    with patch("analysis.baselines.list_records", side_effect=mock_list_records):
        results = await compute_baselines(mock_session)

    # 3 records should produce baselines for both 7d and 30d (same data, all within 7d)
    assert len(results) > 0
    for r in results:
        assert r.sample_count >= 2
