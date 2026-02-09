"""Tests for analysis/reporter.py"""

import pytest
from analysis.anomalies import Anomaly
from analysis.investigations import Investigation, CauseLink
from analysis.correlations import CorrelationsReport, BounceBack, SleepRecoveryQuantified, DayOfWeekPattern
from analysis.reporter import (
    format_morning_brief,
    format_post_workout,
    format_weekly_digest,
    format_alert,
    _fmt_anomaly,
    _fmt_investigation,
)


def _anomaly(metric="hrv", severity="watch", z=-2.0):
    return Anomaly(
        metric=metric, severity=severity, z_score=z,
        current_value=30.0, baseline_mean=50.0, baseline_std=10.0,
        direction="below", sustained_days=1,
        description=f"{metric} low", fingerprint=f"fp_{metric}",
    )


def _investigation(anomaly=None):
    a = anomaly or _anomaly()
    return Investigation(
        anomaly=a, hypothesis="sleep_debt", confidence="high",
        cause_chain=[
            CauseLink(metric="sleep_duration", observation="Short sleep (5.5h)", days_prior=0, value=5.5),
            CauseLink(metric="sleep_duration", observation="Short sleep (5.0h)", days_prior=1, value=5.0),
        ],
        recommendation="Prioritize sleep tonight.",
        supporting_signals=2,
    )


class TestMorningBrief:
    def test_green_recovery(self):
        result = format_morning_brief(85, 55, 58, 97.0, 7.5, "Good", [], [])
        assert "🟢" in result
        assert "85%" in result
        assert "All metrics within normal range" in result

    def test_red_recovery(self):
        result = format_morning_brief(25, 30, 70, 95.0, 5.0, None, [], [])
        assert "🔴" in result

    def test_yellow_recovery(self):
        result = format_morning_brief(50, 45, 62, 96.0, 7.0, None, [], [])
        assert "🟡" in result

    def test_with_anomalies(self):
        anomalies = [_anomaly(severity="alert")]
        result = format_morning_brief(30, 25, 72, 92.0, 5.0, None, anomalies, [])
        assert "🚨" in result
        assert "ALERT" in result
        assert "All metrics within normal range" not in result

    def test_with_investigations(self):
        inv = _investigation()
        result = format_morning_brief(30, 25, 72, 92.0, 5.0, None, [_anomaly()], [inv])
        assert "sleep_debt" in result.lower() or "Sleep Debt" in result
        assert "Prioritize sleep" in result

    def test_none_values_handled(self):
        result = format_morning_brief(None, None, None, None, None, None, [], [])
        assert "Morning Brief" in result

    def test_info_anomalies_not_shown(self):
        anomalies = [_anomaly(severity="info")]
        result = format_morning_brief(65, 50, 60, 96.0, 7.0, None, anomalies, [])
        assert "All metrics within normal range" in result


class TestPostWorkout:
    def test_basic(self):
        result = format_post_workout(14.5, 1.2, "Running", 45, 155, 178, 450)
        assert "14.5" in result
        assert "Running" in result
        assert "45 min" in result

    def test_high_strain_advice(self):
        result = format_post_workout(18.0, 2.5, "CrossFit", 60, 165, 190, 700)
        assert "prioritize sleep" in result.lower()

    def test_above_average_z(self):
        result = format_post_workout(16.0, 2.3, None, None, None, None, None)
        assert "above" in result.lower() or "⬆️" in result

    def test_below_average_z(self):
        result = format_post_workout(8.0, -1.5, None, None, None, None, None)
        assert "below" in result.lower() or "↘️" in result

    def test_none_optionals(self):
        result = format_post_workout(12.0, None, None, None, None, None, None)
        assert "12.0" in result


class TestWeeklyDigest:
    def test_basic(self):
        result = format_weekly_digest(65, 50, 7.2, 13.5, "Tuesday", "Friday", None, 3, 1)
        assert "Weekly Digest" in result
        assert "65%" in result
        assert "Tuesday" in result
        assert "3 anomalies" in result

    def test_with_correlations(self):
        s2r = SleepRecoveryQuantified(
            correlation=0.65, p_value=0.01, slope=8.5, intercept=10,
            r_squared=0.42, sample_size=20,
            interpretation="Strong: each extra hour → ~8.5 recovery points",
        )
        bb = BounceBack(avg_days_to_recover=1.5, high_strain_threshold=16, recovery_threshold=60, sample_count=5)
        dow = [DayOfWeekPattern(
            metric="recovery", day_averages={"Mon": 70, "Fri": 50},
            best_day="Monday", worst_day="Friday", significant=True,
        )]
        report = CorrelationsReport(day_of_week=dow, bounce_back=bb, sleep_to_recovery=s2r, notable_correlations=[])
        result = format_weekly_digest(65, 50, 7.2, 13.5, None, None, report, 0, 0)
        assert "8.5" in result
        assert "1.5 days" in result
        assert "Monday" in result

    def test_none_values(self):
        result = format_weekly_digest(None, None, None, None, None, None, None, 0, 0)
        assert "Weekly Digest" in result


class TestAlert:
    def test_basic(self):
        result = format_alert(_anomaly(severity="alert"))
        assert "🚨" in result

    def test_with_investigation(self):
        a = _anomaly(severity="alert")
        inv = _investigation(a)
        result = format_alert(a, inv)
        assert "Likely cause" in result


class TestFormatHelpers:
    def test_fmt_anomaly(self):
        result = _fmt_anomaly(_anomaly(severity="watch"))
        assert "⚠️" in result
        assert "WATCH" in result

    def test_fmt_investigation(self):
        result = _fmt_investigation(_investigation())
        assert "Sleep Debt" in result
        assert "Today" in result
