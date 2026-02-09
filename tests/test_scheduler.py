"""Tests for analysis/scheduler.py"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from analysis.scheduler import (
    _already_sent,
    _alert_on_cooldown,
    handle_sleep_event,
    handle_workout_event,
    handle_recovery_event,
)


class TestAlreadySent:
    @pytest.mark.asyncio
    async def test_not_sent(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result
        assert await _already_sent(session, "morning_brief", "2026-02-09") is False

    @pytest.mark.asyncio
    async def test_already_sent(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()  # truthy = exists
        session.execute.return_value = mock_result
        assert await _already_sent(session, "morning_brief", "2026-02-09") is True


class TestAlertCooldown:
    @pytest.mark.asyncio
    async def test_no_prior(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result
        assert await _alert_on_cooldown(session, "fp123") is False

    @pytest.mark.asyncio
    async def test_on_cooldown(self):
        session = AsyncMock()
        row = MagicMock()
        row.last_sent = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row
        session.execute.return_value = mock_result
        assert await _alert_on_cooldown(session, "fp123") is True

    @pytest.mark.asyncio
    async def test_cooldown_expired(self):
        session = AsyncMock()
        row = MagicMock()
        row.last_sent = datetime.now(timezone.utc) - timedelta(hours=24)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row
        session.execute.return_value = mock_result
        assert await _alert_on_cooldown(session, "fp123") is False


@pytest.mark.asyncio
async def test_handle_sleep_event():
    """Smoke test: sleep event triggers morning brief (mocked)."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None  # not already sent
    mock_session.execute.return_value = mock_result
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("analysis.scheduler.async_session", return_value=mock_ctx), \
         patch("analysis.scheduler.compute_baselines", return_value=[]), \
         patch("analysis.scheduler.get_all_baselines", return_value={}), \
         patch("analysis.scheduler.analyze_sleep") as mock_sleep, \
         patch("analysis.scheduler.analyze_recovery") as mock_recovery, \
         patch("analysis.scheduler.detect_all_anomalies", return_value=[]), \
         patch("analysis.scheduler.investigate_anomalies", return_value=[]), \
         patch("analysis.scheduler.send_telegram", return_value=False), \
         patch("analysis.scheduler.save_insight"):

        from analysis.sleep import SleepReport
        from analysis.recovery import RecoveryReport
        mock_sleep.return_value = SleepReport(
            date=datetime.now(timezone.utc), last_sleep_duration_hrs=7.0,
            duration_vs_baseline_pct=None, duration_z_score=None,
            efficiency_pct=92.0, trend=None, stages=None,
            consistency=None, debt=None, flags=[],
        )
        mock_recovery.return_value = RecoveryReport(
            date=datetime.now(timezone.utc), latest_score=70.0,
            score_vs_baseline_pct=None, score_z=None,
            trend=None, prediction=None, sleep_correlation=None, flags=[],
        )

        await handle_sleep_event({"id": "test-sleep-123"})

    # Should have attempted to save insight
    mock_sleep.assert_called_once()
    mock_recovery.assert_called_once()


@pytest.mark.asyncio
async def test_handle_workout_event():
    """Smoke test: workout event triggers post-workout (mocked)."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("analysis.scheduler.async_session", return_value=mock_ctx), \
         patch("analysis.scheduler.analyze_strain") as mock_strain, \
         patch("analysis.scheduler.analyze_recovery") as mock_recovery, \
         patch("analysis.scheduler.send_telegram", return_value=False), \
         patch("analysis.scheduler.save_insight"):

        from analysis.strain import StrainReport
        from analysis.recovery import RecoveryReport
        mock_strain.return_value = StrainReport(
            date=datetime.now(timezone.utc), latest_day_strain=12.0,
            strain_vs_baseline_pct=None, strain_z=None,
            acute_chronic=None, balance=None, overtraining=None,
            workout_dist=None, flags=[],
        )
        mock_recovery.return_value = RecoveryReport(
            date=datetime.now(timezone.utc), latest_score=70.0,
            score_vs_baseline_pct=None, score_z=None,
            trend=None, prediction=None, sleep_correlation=None, flags=[],
        )

        await handle_workout_event({"id": "test-workout-123"})

    mock_strain.assert_called_once()
