"""Tests for analysis/reporter.py"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from analysis.reporter import (
    format_morning_brief,
    format_post_workout,
    format_weekly_digest,
    format_alert,
    send_telegram,
    save_insight,
)
from analysis.sleep import SleepReport, SleepTrend, StageAnalysis, ConsistencyAnalysis, SleepDebt
from analysis.recovery import RecoveryReport, RecoveryTrend, RecoveryPrediction
from analysis.strain import StrainReport, AcuteChronicLoad, StrainRecoveryBalance, OvertrainingSignal, WorkoutDistribution
from analysis.anomalies import Anomaly
from analysis.investigations import Investigation, CauseLink
from analysis.correlations import CorrelationsReport, SleepRecoveryQuantified, BounceBack


NOW = datetime.now(timezone.utc)


def _sleep_report(**kwargs):
    defaults = dict(
        date=NOW, last_sleep_duration_hrs=7.2, duration_vs_baseline_pct=-5.0,
        duration_z_score=-0.4, efficiency_pct=92.0,
        trend=SleepTrend(duration_slope_hrs_per_day=-0.1, duration_7d_avg_hrs=7.0, duration_14d_avg_hrs=7.3, direction="stable"),
        stages=StageAnalysis(current_rem_pct=28.0, current_deep_pct=15.0, current_light_pct=57.0,
                             baseline_rem_pct=25.0, baseline_deep_pct=16.0, baseline_light_pct=59.0,
                             rem_z_score=0.5, deep_z_score=-0.3, rem_flag=None, deep_flag=None),
        consistency=ConsistencyAnalysis(bedtime_std_minutes=25, waketime_std_minutes=20, consistency_score=75, consistency_recovery_correlation=None),
        debt=SleepDebt(baseline_need_hrs=7.8, current_debt_hrs=3.2, trend="stable", days_analyzed=7),
        flags=[],
    )
    defaults.update(kwargs)
    return SleepReport(**defaults)


def _recovery_report(**kwargs):
    defaults = dict(
        date=NOW, latest_score=72.0, score_vs_baseline_pct=3.0, score_z=0.2,
        trend=RecoveryTrend(slope_per_day=0.5, avg_7d=70.0, avg_14d=68.0, direction="stable"),
        prediction=None, sleep_correlation=None, flags=[],
    )
    defaults.update(kwargs)
    return RecoveryReport(**defaults)


def _strain_report(**kwargs):
    defaults = dict(
        date=NOW, latest_day_strain=14.5, strain_vs_baseline_pct=15.0, strain_z=0.8,
        acute_chronic=AcuteChronicLoad(acute_7d=13.0, chronic_30d=11.0, ratio=1.18, risk_level="moderate"),
        balance=StrainRecoveryBalance(avg_strain_7d=13.0, avg_recovery_7d=70.0, strain_recovery_ratio=0.88, ratio_trend="balanced", drift_direction=None),
        overtraining=OvertrainingSignal(detected=False, strain_trend_slope=0.1, recovery_trend_slope=-0.2, days_analyzed=14, confidence="low"),
        workout_dist=WorkoutDistribution(total_workouts=5, avg_strain_per_workout=10.0, high_intensity_count=1, moderate_count=3, low_count=1, avg_duration_minutes=45, sport_breakdown={"activity": 5}),
        flags=[],
    )
    defaults.update(kwargs)
    return StrainReport(**defaults)


def _anomaly(metric="recovery", severity="watch", value=45.0):
    return Anomaly(metric=metric, severity=severity, z_score=-2.0, current_value=value,
                   baseline_mean=70.0, baseline_std=10.0, direction="below",
                   sustained_days=1, description=f"{metric} below baseline", fingerprint="abc")


class TestFormatMorningBrief:
    def test_basic(self):
        msg = format_morning_brief(_sleep_report(), _recovery_report(), [], [])
        assert "Morning Brief" in msg
        assert "Sleep" in msg
        assert "Recovery" in msg
        assert "7.2h" in msg

    def test_with_anomalies(self):
        anomalies = [_anomaly()]
        inv = Investigation(
            anomaly=anomalies[0],
            cause_chain=[CauseLink(factor="Short sleep", evidence="5h vs 7.5h", contribution="likely")],
            summary="Low recovery likely driven by short sleep.",
            confidence="medium",
        )
        msg = format_morning_brief(_sleep_report(), _recovery_report(latest_score=25.0), anomalies, [inv])
        assert "Alert" in msg
        assert "Rest day" in msg

    def test_high_recovery(self):
        msg = format_morning_brief(_sleep_report(), _recovery_report(latest_score=85.0), [], [])
        assert "high intensity" in msg.lower()


class TestFormatPostWorkout:
    def test_basic(self):
        msg = format_post_workout(_strain_report(), _recovery_report())
        assert "Post-Workout" in msg
        assert "14.5" in msg

    def test_high_strain(self):
        msg = format_post_workout(_strain_report(latest_day_strain=18.0), _recovery_report())
        assert "lower recovery" in msg.lower()

    def test_with_overtraining(self):
        report = _strain_report(
            overtraining=OvertrainingSignal(detected=True, strain_trend_slope=0.8, recovery_trend_slope=-1.5, days_analyzed=14, confidence="high"),
            flags=["Overtraining signal detected"],
        )
        msg = format_post_workout(report, _recovery_report())
        assert "Overtraining" in msg


class TestFormatWeeklyDigest:
    def test_basic(self):
        corr = CorrelationsReport(
            day_of_week=[],
            bounce_back=BounceBack(avg_days_to_recover=1.5, high_strain_threshold=15, recovery_threshold=65, sample_count=4),
            sleep_to_recovery=SleepRecoveryQuantified(correlation=0.6, p_value=0.01, slope=5.2, intercept=30, r_squared=0.36, sample_size=14, interpretation="Strong: each extra hour → ~5 recovery points"),
            notable_correlations=[],
        )
        msg = format_weekly_digest(_sleep_report(), _recovery_report(), _strain_report(), corr, [])
        assert "Weekly Digest" in msg
        assert "Trends" in msg
        assert "bounce-back" in msg.lower()


class TestFormatAlert:
    def test_basic(self):
        a = _anomaly(severity="alert", metric="spo2", value=91.0)
        msg = format_alert(a)
        assert "SPO2" in msg
        assert "🔴" in msg

    def test_with_investigation(self):
        a = _anomaly()
        inv = Investigation(
            anomaly=a,
            cause_chain=[CauseLink(factor="Short sleep", evidence="5h", contribution="likely")],
            summary="Low recovery from short sleep.",
            confidence="medium",
        )
        msg = format_alert(a, inv)
        assert "Investigation" in msg
        assert "Short sleep" in msg


@pytest.mark.asyncio
async def test_send_telegram_no_config():
    """Without config, should return False gracefully."""
    result = await send_telegram("test")
    assert result is False


@pytest.mark.asyncio
async def test_save_insight():
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    await save_insight(session, "morning_brief", {"test": True}, "test message")
    session.add.assert_called_once()
    session.commit.assert_called_once()
