"""
analysis/baselines.py — Compute rolling 7d and 30d baselines for key metrics.

Metrics: hrv, rhr, sleep_duration, strain, recovery, spo2
Reads from WHOOP API (via whoop_client), stores results in Postgres baselines table.
Designed for incremental updates — call compute_baselines() when new data arrives.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional

import numpy as np
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import Baseline
from api.whoop_client import list_records

logger = logging.getLogger(__name__)

USER_ID = "32850214"

WINDOWS = {"7d": 7, "30d": 30}

METRICS = ["hrv", "rhr", "sleep_duration", "strain", "recovery", "spo2"]


@dataclass
class BaselineResult:
    metric: str
    window: str
    mean: float
    std: float
    sample_count: int
    window_start: datetime
    window_end: datetime


def _extract_metric_from_recovery(record: dict, metric: str) -> Optional[float]:
    """Extract a metric value from a recovery API record."""
    score = record.get("score")
    if not score:
        return None
    mapping = {
        "hrv": "hrv_rmssd_milli",
        "rhr": "resting_heart_rate",
        "recovery": "recovery_score",
        "spo2": "spo2_percentage",
    }
    key = mapping.get(metric)
    if key:
        val = score.get(key)
        return float(val) if val is not None else None
    return None


def _extract_sleep_duration(record: dict) -> Optional[float]:
    """Extract total sleep duration in hours from a sleep API record."""
    score = record.get("score")
    if not score:
        return None
    stages = score.get("stage_summary")
    if not stages:
        return None
    total_milli = (
        stages.get("total_light_sleep_time_milli", 0)
        + stages.get("total_slow_wave_sleep_time_milli", 0)
        + stages.get("total_rem_sleep_time_milli", 0)
    )
    return total_milli / 3_600_000  # convert ms to hours


def _extract_strain(record: dict) -> Optional[float]:
    """Extract strain from a cycle API record."""
    score = record.get("score")
    if not score:
        return None
    val = score.get("strain")
    return float(val) if val is not None else None


def _record_date(record: dict) -> Optional[datetime]:
    """Parse the created_at timestamp from a record."""
    ts = record.get("created_at")
    if ts:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return None


async def _fetch_recovery_data(days: int) -> list[dict]:
    """Fetch recovery records for the last N days from the API."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    all_records = []
    next_token = None
    while True:
        data = await list_records(
            USER_ID, "recovery", limit=25, start=start, end=end, next_token=next_token
        )
        records = data.get("records", [])
        all_records.extend(records)
        next_token = data.get("next_token")
        if not next_token or not records:
            break
    return all_records


async def _fetch_sleep_data(days: int) -> list[dict]:
    """Fetch sleep records for the last N days from the API."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    all_records = []
    next_token = None
    while True:
        data = await list_records(
            USER_ID, "sleep", limit=25, start=start, end=end, next_token=next_token
        )
        records = data.get("records", [])
        all_records.extend(records)
        next_token = data.get("next_token")
        if not next_token or not records:
            break
    return all_records


async def _fetch_cycle_data(days: int) -> list[dict]:
    """Fetch cycle records for the last N days from the API."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    all_records = []
    next_token = None
    while True:
        data = await list_records(
            USER_ID, "cycle", limit=25, start=start, end=end, next_token=next_token
        )
        records = data.get("records", [])
        all_records.extend(records)
        next_token = data.get("next_token")
        if not next_token or not records:
            break
    return all_records


def compute_baseline_from_values(
    values: list[float],
    metric: str,
    window: str,
    window_start: datetime,
    window_end: datetime,
) -> Optional[BaselineResult]:
    """Compute mean and std from a list of values."""
    if len(values) < 2:
        return None
    arr = np.array(values)
    return BaselineResult(
        metric=metric,
        window=window,
        mean=float(np.mean(arr)),
        std=float(np.std(arr, ddof=1)),
        sample_count=len(values),
        window_start=window_start,
        window_end=window_end,
    )


async def compute_baselines(session: AsyncSession) -> list[BaselineResult]:
    """
    Compute all baselines and store in Postgres.
    Returns the list of computed BaselineResult objects.
    """
    now = datetime.now(timezone.utc)
    results: list[BaselineResult] = []

    # Fetch data once for max window
    recovery_data = await _fetch_recovery_data(30)
    sleep_data = await _fetch_sleep_data(30)
    cycle_data = await _fetch_cycle_data(30)

    logger.info(
        "Fetched %d recovery, %d sleep, %d cycle records for baselines",
        len(recovery_data), len(sleep_data), len(cycle_data),
    )

    for window_name, days in WINDOWS.items():
        window_start = now - timedelta(days=days)

        # Recovery-derived metrics: hrv, rhr, recovery, spo2
        windowed_recovery = [
            r for r in recovery_data
            if (d := _record_date(r)) and d >= window_start
        ]
        for metric in ["hrv", "rhr", "recovery", "spo2"]:
            values = [
                v for r in windowed_recovery
                if (v := _extract_metric_from_recovery(r, metric)) is not None
            ]
            bl = compute_baseline_from_values(values, metric, window_name, window_start, now)
            if bl:
                results.append(bl)

        # Sleep duration
        windowed_sleep = [
            r for r in sleep_data
            if (d := _record_date(r)) and d >= window_start
            and not r.get("nap", False)  # exclude naps
        ]
        values = [
            v for r in windowed_sleep
            if (v := _extract_sleep_duration(r)) is not None
        ]
        bl = compute_baseline_from_values(values, "sleep_duration", window_name, window_start, now)
        if bl:
            results.append(bl)

        # Strain (from cycles)
        windowed_cycles = [
            r for r in cycle_data
            if (d := _record_date(r)) and d >= window_start
            and r.get("score_state") == "SCORED"
        ]
        values = [
            v for r in windowed_cycles
            if (v := _extract_strain(r)) is not None
        ]
        bl = compute_baseline_from_values(values, "strain", window_name, window_start, now)
        if bl:
            results.append(bl)

    # Store in Postgres — delete old baselines for this user, insert fresh
    await session.execute(
        delete(Baseline).where(Baseline.user_id == USER_ID)
    )
    for bl in results:
        session.add(Baseline(
            user_id=USER_ID,
            metric=bl.metric,
            window=bl.window,
            mean=bl.mean,
            std=bl.std,
            sample_count=bl.sample_count,
            window_start=bl.window_start,
            window_end=bl.window_end,
        ))
    await session.commit()
    logger.info("Stored %d baseline records for user %s", len(results), USER_ID)

    return results


async def get_baseline(
    session: AsyncSession,
    metric: str,
    window: str = "30d",
    user_id: str = USER_ID,
) -> Optional[BaselineResult]:
    """Retrieve the latest stored baseline for a metric/window."""
    from sqlalchemy import select
    stmt = (
        select(Baseline)
        .where(Baseline.user_id == user_id)
        .where(Baseline.metric == metric)
        .where(Baseline.window == window)
        .order_by(Baseline.computed_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if not row:
        return None
    return BaselineResult(
        metric=row.metric,
        window=row.window,
        mean=row.mean,
        std=row.std,
        sample_count=row.sample_count,
        window_start=row.window_start,
        window_end=row.window_end,
    )


async def get_all_baselines(
    session: AsyncSession,
    user_id: str = USER_ID,
) -> dict[str, dict[str, BaselineResult]]:
    """Get all baselines as {metric: {window: BaselineResult}}."""
    out: dict[str, dict[str, BaselineResult]] = {}
    for metric in METRICS:
        out[metric] = {}
        for window in WINDOWS:
            bl = await get_baseline(session, metric, window, user_id)
            if bl:
                out[metric][window] = bl
    return out
