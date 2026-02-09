"""Tests for analysis/investigations.py"""

import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone, timedelta

from analysis.anomalies import Anomaly
from analysis.investigations import (
    investigate_anomaly,
    investigate_all,
    _get_recent_context,
    _check_overtraining,
    _check_sleep_debt,
    _check_illness,
    _check_stress,
    CauseLink,
    Investigation,
)


def _make_anomaly(metric="hrv", severity="watch", z_score=-2.1, current=30.0,
                   mean=50.0, std=10.0, direction="below", sustained=1):
    return Anomaly(
        metric=metric, severity=severity, z_score=z_score,
        current_value=current, baseline_mean=mean, baseline_std=std,
        direction=direction, sustained_days=sustained,
        description=f"Test anomaly for {metric}",
        fingerprint=f"fp_{metric}",
    )


def _make_context(days=5, **overrides):
    """Build a multi-day context dict. overrides apply to day 0 (latest)."""
    base = datetime(2026, 2, 9, tzinfo=timezone.utc)
    ctx = {}
    for i in range(days):
        d = (base - timedelta(days=i)).strftime("%Y-%m-%d")
        ctx[d] = {
            "hrv": 50.0, "rhr": 60.0, "recovery": 65.0,
            "spo2": 96.0, "sleep_duration": 7.5, "strain": 12.0,
        }
    # Apply overrides to latest day
    latest = (base).strftime("%Y-%m-%d")
    ctx[latest].update(overrides)
    return ctx


# --- investigate_anomaly routing ---

class TestOvertraining:
    def test_detects_high_strain_pattern(self):
        ctx = _make_context(5, strain=18.0, hrv=30.0, rhr=72.0)
        # Also add high strain yesterday
        dates = sorted(ctx.keys(), reverse=True)
        ctx[dates[1]]["strain"] = 17.0
        anomaly = _make_anomaly("hrv")
        result = _check_overtraining(anomaly, ctx)
        assert result is not None
        assert result.hypothesis == "overtraining"
        assert len(result.cause_chain) >= 2

    def test_no_detection_with_low_strain(self):
        ctx = _make_context(5, strain=8.0)
        anomaly = _make_anomaly("hrv")
        result = _check_overtraining(anomaly, ctx)
        assert result is None

    def test_wrong_metric_skipped(self):
        ctx = _make_context(5, strain=18.0)
        anomaly = _make_anomaly("spo2")
        result = _check_overtraining(anomaly, ctx)
        assert result is None


class TestSleepDebt:
    def test_detects_multiple_short_nights(self):
        ctx = _make_context(5)
        dates = sorted(ctx.keys(), reverse=True)
        for d in dates[:3]:
            ctx[d]["sleep_duration"] = 5.5
        anomaly = _make_anomaly("recovery")
        result = _check_sleep_debt(anomaly, ctx)
        assert result is not None
        assert result.hypothesis == "sleep_debt"
        assert result.confidence == "high"

    def test_one_short_night_insufficient(self):
        ctx = _make_context(5)
        dates = sorted(ctx.keys(), reverse=True)
        ctx[dates[0]]["sleep_duration"] = 5.0
        anomaly = _make_anomaly("recovery")
        result = _check_sleep_debt(anomaly, ctx)
        assert result is None

    def test_wrong_metric_skipped(self):
        ctx = _make_context(5)
        dates = sorted(ctx.keys(), reverse=True)
        for d in dates[:3]:
            ctx[d]["sleep_duration"] = 5.0
        anomaly = _make_anomaly("strain")
        result = _check_sleep_debt(anomaly, ctx)
        assert result is None


class TestIllness:
    def test_detects_spo2_plus_rhr_spike(self):
        ctx = _make_context(5, spo2=91.0, rhr=72.0, hrv=30.0)
        # Previous days normal
        dates = sorted(ctx.keys(), reverse=True)
        ctx[dates[1]]["rhr"] = 58.0
        ctx[dates[1]]["hrv"] = 55.0
        anomaly = _make_anomaly("spo2")
        result = _check_illness(anomaly, ctx)
        assert result is not None
        assert result.hypothesis == "illness_onset"

    def test_no_detection_with_normal_vitals(self):
        ctx = _make_context(5)
        anomaly = _make_anomaly("spo2")
        result = _check_illness(anomaly, ctx)
        assert result is None


class TestStress:
    def test_detects_hrv_down_sleep_ok_strain_normal(self):
        ctx = _make_context(5, hrv=28.0, sleep_duration=8.0, strain=10.0)
        anomaly = _make_anomaly("hrv")
        result = _check_stress(anomaly, ctx)
        assert result is not None
        assert result.hypothesis == "stress_response"

    def test_no_detection_if_sleep_short(self):
        ctx = _make_context(5, hrv=28.0, sleep_duration=5.0, strain=10.0)
        anomaly = _make_anomaly("hrv")
        result = _check_stress(anomaly, ctx)
        assert result is None

    def test_no_detection_if_strain_high(self):
        ctx = _make_context(5, hrv=28.0, sleep_duration=8.0, strain=18.0)
        anomaly = _make_anomaly("hrv")
        result = _check_stress(anomaly, ctx)
        assert result is None


class TestInvestigateAnomaly:
    def test_picks_highest_confidence(self):
        # Context fits both sleep_debt and stress — sleep_debt should win with 3 short nights
        ctx = _make_context(5, hrv=28.0, sleep_duration=5.5, strain=10.0)
        dates = sorted(ctx.keys(), reverse=True)
        ctx[dates[1]]["sleep_duration"] = 5.0
        ctx[dates[2]]["sleep_duration"] = 5.0
        anomaly = _make_anomaly("hrv")
        result = investigate_anomaly(anomaly, ctx)
        assert result is not None
        assert result.hypothesis == "sleep_debt"

    def test_returns_none_when_nothing_fits(self):
        ctx = _make_context(5)  # Everything normal
        anomaly = _make_anomaly("strain", direction="above")
        result = investigate_anomaly(anomaly, ctx)
        assert result is None

    def test_empty_context(self):
        anomaly = _make_anomaly("hrv")
        result = investigate_anomaly(anomaly, {})
        assert result is None


class TestInvestigateAll:
    @pytest.mark.asyncio
    async def test_runs_all_anomalies(self):
        anomalies = [
            _make_anomaly("hrv"),
            _make_anomaly("recovery", z_score=-1.8, current=40.0),
        ]

        def make_recovery(date, hrv=50, rhr=60, recovery=65, spo2=96):
            return {
                "created_at": date.isoformat(),
                "score": {
                    "hrv_rmssd_milli": hrv, "resting_heart_rate": rhr,
                    "recovery_score": recovery, "spo2_percentage": spo2,
                },
            }

        def make_sleep(date, dur_h=7.5):
            ms = int(dur_h * 3600 * 1000)
            return {
                "start": date.isoformat(),
                "end": (date + timedelta(hours=dur_h)).isoformat(),
                "score": {"stage_summary": {
                    "total_light_sleep_time_milli": ms // 2,
                    "total_slow_wave_sleep_time_milli": ms // 4,
                    "total_rem_sleep_time_milli": ms // 4,
                    "total_awake_time_milli": 0,
                }},
            }

        def make_cycle(date, strain=12.0):
            return {"created_at": date.isoformat(), "score": {"strain": strain}}

        base = datetime(2026, 2, 9, tzinfo=timezone.utc)
        recoveries = [make_recovery(base - timedelta(days=i), hrv=30 if i == 0 else 50) for i in range(14)]
        sleeps = [make_sleep(base - timedelta(days=i), dur_h=5.5 if i < 3 else 7.5) for i in range(14)]
        cycles = [make_cycle(base - timedelta(days=i)) for i in range(14)]

        with patch("analysis.investigations._fetch_recovery_data", new_callable=AsyncMock, return_value=recoveries), \
             patch("analysis.investigations._fetch_sleep_data", new_callable=AsyncMock, return_value=sleeps), \
             patch("analysis.investigations._fetch_cycle_data", new_callable=AsyncMock, return_value=cycles):
            results = await investigate_all(anomalies)
            assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_empty_anomalies(self):
        results = await investigate_all([])
        assert results == []


class TestGetRecentContext:
    def test_builds_context_from_records(self):
        base = datetime(2026, 2, 9, tzinfo=timezone.utc)
        recovery = [{"created_at": base.isoformat(), "score": {
            "hrv_rmssd_milli": 45, "resting_heart_rate": 62,
            "recovery_score": 55, "spo2_percentage": 96,
        }}]
        sleep = [{"start": base.isoformat(),
                   "end": (base + timedelta(hours=7)).isoformat(),
                   "score": {"stage_summary": {
                       "total_light_sleep_time_milli": 9000000,
                       "total_slow_wave_sleep_time_milli": 5000000,
                       "total_rem_sleep_time_milli": 5000000,
                       "total_awake_time_milli": 1000000,
                   }}}]
        cycles = [{"created_at": base.isoformat(), "score": {"strain": 14.5}}]

        ctx = _get_recent_context(recovery, sleep, cycles)
        key = base.strftime("%Y-%m-%d")
        assert key in ctx
        assert ctx[key]["hrv"] == 45
        assert ctx[key]["strain"] == 14.5
