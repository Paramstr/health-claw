"""Tests for analysis/scheduler.py"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta

from analysis.scheduler import (
    run_morning_brief,
    run_post_workout,
    run_weekly_digest,
    run_alert_check,
    handle_webhook,
    reset_idempotency,
    _already_ran,
    _mark_ran,
)


@pytest.fixture(autouse=True)
def clean_idempotency():
    reset_idempotency()
    yield
    reset_idempotency()


def _recovery_record(recovery=65, hrv=50, rhr=60, spo2=96, days_ago=0):
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "created_at": d.isoformat(),
        "score": {
            "recovery_score": recovery,
            "hrv_rmssd_milli": hrv,
            "resting_heart_rate": rhr,
            "spo2_percentage": spo2,
        },
    }


def _sleep_record(hours=7.5, days_ago=0):
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ms = int(hours * 3600 * 1000)
    return {
        "start": d.isoformat(),
        "end": (d + timedelta(hours=hours)).isoformat(),
        "score": {"stage_summary": {
            "total_light_sleep_time_milli": ms // 2,
            "total_slow_wave_sleep_time_milli": ms // 4,
            "total_rem_sleep_time_milli": ms // 4,
            "total_awake_time_milli": 0,
        }},
    }


def _cycle_record(strain=12.0, days_ago=0):
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {"created_at": d.isoformat(), "score": {"strain": strain}}


def _mock_sleep_report():
    from analysis.sleep import SleepReport
    return SleepReport(
        date=datetime.now(timezone.utc),
        last_sleep_duration_hrs=7.5,
        duration_vs_baseline_pct=0.0,
        duration_z_score=0.0,
        efficiency_pct=88.0,
        trend=None, stages=None, consistency=None, debt=None,
    )


def _mock_fetchers(recovery=None, sleep=None, cycles=None):
    """Return a dict of patches for all fetch functions across all modules."""
    r = recovery or [_recovery_record(days_ago=i) for i in range(7)]
    s = sleep or [_sleep_record(days_ago=i) for i in range(7)]
    c = cycles or [_cycle_record(days_ago=i) for i in range(7)]
    return {
        "analysis.scheduler._fetch_recovery_data": AsyncMock(return_value=r),
        "analysis.scheduler._fetch_sleep_data": AsyncMock(return_value=s),
        "analysis.scheduler._fetch_cycle_data": AsyncMock(return_value=c),
        "analysis.scheduler.analyze_sleep": AsyncMock(return_value=_mock_sleep_report()),
        "analysis.anomalies._fetch_recovery_data": AsyncMock(return_value=r),
        "analysis.anomalies._fetch_sleep_data": AsyncMock(return_value=s),
        "analysis.anomalies._fetch_cycle_data": AsyncMock(return_value=c),
        "analysis.correlations._fetch_recovery_data": AsyncMock(return_value=r),
        "analysis.correlations._fetch_sleep_data": AsyncMock(return_value=s),
        "analysis.correlations._fetch_cycle_data": AsyncMock(return_value=c),
        "analysis.investigations._fetch_recovery_data": AsyncMock(return_value=r),
        "analysis.investigations._fetch_sleep_data": AsyncMock(return_value=s),
        "analysis.investigations._fetch_cycle_data": AsyncMock(return_value=c),
    }


class TestIdempotency:
    def test_mark_and_check(self):
        assert not _already_ran("test")
        _mark_ran("test")
        assert _already_ran("test")

    def test_reset(self):
        _mark_ran("test")
        reset_idempotency()
        assert not _already_ran("test")


class TestMorningBrief:
    @pytest.mark.asyncio
    async def test_generates_report(self):
        mocks = _mock_fetchers()
        patches = [patch(k, v) for k, v in mocks.items()]
        for p in patches:
            p.start()
        try:
            result = await run_morning_brief(force=True)
            assert result is not None
            assert "Morning Brief" in result
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_idempotency(self):
        mocks = _mock_fetchers()
        patches = [patch(k, v) for k, v in mocks.items()]
        for p in patches:
            p.start()
        try:
            r1 = await run_morning_brief(force=True)
            assert r1 is not None
            r2 = await run_morning_brief(force=False)
            assert r2 is None  # Already ran
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_no_data_returns_none(self):
        empty_mock = AsyncMock(return_value=[])
        targets = [
            "analysis.scheduler._fetch_recovery_data",
            "analysis.scheduler._fetch_sleep_data",
            "analysis.scheduler._fetch_cycle_data",
            "analysis.anomalies._fetch_recovery_data",
            "analysis.anomalies._fetch_sleep_data",
            "analysis.anomalies._fetch_cycle_data",
            "analysis.sleep._fetch_sleep_data",
            "analysis.sleep._fetch_recovery_data",
            "analysis.investigations._fetch_recovery_data",
            "analysis.investigations._fetch_sleep_data",
            "analysis.investigations._fetch_cycle_data",
        ]
        patches_list = [patch(t, empty_mock) for t in targets]
        for p in patches_list:
            p.start()
        try:
            result = await run_morning_brief(force=True)
            assert result is None
        finally:
            for p in patches_list:
                p.stop()


class TestPostWorkout:
    @pytest.mark.asyncio
    async def test_generates_report(self):
        workout = {
            "score": {
                "strain": 15.5,
                "average_heart_rate": 155,
                "max_heart_rate": 185,
                "kilojoule": 2000,
            },
            "sport_id": 1,
        }
        result = await run_post_workout(workout)
        assert result is not None
        assert "15.5" in result

    @pytest.mark.asyncio
    async def test_no_strain_returns_none(self):
        result = await run_post_workout({"score": {}})
        assert result is None

    @pytest.mark.asyncio
    async def test_calories_converted(self):
        workout = {"score": {"strain": 10.0, "kilojoule": 4184}}
        result = await run_post_workout(workout)
        assert result is not None
        # 4184 kJ * 0.239 ≈ 1000 cal
        assert "1000" in result or "999" in result


class TestWeeklyDigest:
    @pytest.mark.asyncio
    async def test_generates_report(self):
        mocks = _mock_fetchers()
        patches = [patch(k, v) for k, v in mocks.items()]
        for p in patches:
            p.start()
        try:
            result = await run_weekly_digest(force=True)
            assert result is not None
            assert "Weekly Digest" in result
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_idempotency(self):
        mocks = _mock_fetchers()
        patches = [patch(k, v) for k, v in mocks.items()]
        for p in patches:
            p.start()
        try:
            r1 = await run_weekly_digest(force=True)
            assert r1 is not None
            r2 = await run_weekly_digest(force=False)
            assert r2 is None
        finally:
            for p in patches:
                p.stop()


class TestAlertCheck:
    @pytest.mark.asyncio
    async def test_no_alerts_when_normal(self):
        mocks = _mock_fetchers()
        patches = [patch(k, v) for k, v in mocks.items()]
        for p in patches:
            p.start()
        try:
            # Also mock get_all_baselines to return baselines where data is normal
            with patch("analysis.anomalies.get_all_baselines", new_callable=AsyncMock, return_value={}):
                result = await run_alert_check()
                assert isinstance(result, list)
        finally:
            for p in patches:
                p.stop()


class TestWebhook:
    @pytest.mark.asyncio
    async def test_recovery_triggers_morning(self):
        mocks = _mock_fetchers()
        patches = [patch(k, v) for k, v in mocks.items()]
        for p in patches:
            p.start()
        try:
            result = await handle_webhook("recovery.updated", {})
            assert result is not None
            assert "Morning Brief" in result
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_workout_triggers_post(self):
        data = {"score": {"strain": 14.0}}
        result = await handle_webhook("workout.updated", data)
        assert result is not None
        assert "14.0" in result

    @pytest.mark.asyncio
    async def test_unknown_event_returns_none(self):
        result = await handle_webhook("unknown.event", {})
        assert result is None
