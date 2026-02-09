import logging
from typing import Optional
from datetime import datetime, timezone, timedelta

import httpx
from api.oauth import get_valid_access_token

logger = logging.getLogger(__name__)

BASE_URL = "https://api.prod.whoop.com/developer/v2"

OBJECT_ENDPOINTS = {
    "sleep": "/activity/sleep/{id}",
    "workout": "/activity/workout/{id}",
    "cycle": "/cycle/{id}",
}

LIST_ENDPOINTS = {
    "sleep": "/activity/sleep",
    "workout": "/activity/workout",
    "recovery": "/recovery",
    "cycle": "/cycle",
}

# Recovery is accessed via cycle: /v2/cycle/{cycleId}/recovery
CYCLE_SUB_ENDPOINTS = {
    "recovery": "/cycle/{cycle_id}/recovery",
    "sleep": "/cycle/{cycle_id}/sleep",
}


async def _authed_client(user_id: str) -> tuple[httpx.AsyncClient, dict]:
    token = await get_valid_access_token(user_id)
    return httpx.AsyncClient(), {"Authorization": f"Bearer {token}"}


# --- single object by id ---

async def fetch_object(user_id: str, event_type: str, object_id: str) -> dict:
    kind = event_type.split(".")[0]
    path_template = OBJECT_ENDPOINTS.get(kind)
    if path_template is None:
        raise ValueError(f"Unknown event kind: {kind}")

    path = path_template.format(id=object_id)
    token = await get_valid_access_token(user_id)

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        logger.info("Fetched %s %s for user %s", kind, object_id, user_id)
        return resp.json()


# --- list endpoints ---

async def list_records(
    user_id: str,
    kind: str,
    limit: int = 10,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    next_token: Optional[str] = None,
) -> dict:
    """List records from WHOOP API. kind: sleep, workout, recovery, cycle."""
    path = LIST_ENDPOINTS.get(kind)
    if path is None:
        raise ValueError(f"Unknown kind: {kind}. Valid: {list(LIST_ENDPOINTS)}")

    token = await get_valid_access_token(user_id)
    params = {"limit": limit}
    if start:
        params["start"] = start.isoformat()
    if end:
        params["end"] = end.isoformat()
    if next_token:
        params["nextToken"] = next_token

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_sleep(user_id: str, limit: int = 5) -> list[dict]:
    data = await list_records(user_id, "sleep", limit=limit)
    return data.get("records", [])


async def get_workouts(user_id: str, limit: int = 5) -> list[dict]:
    data = await list_records(user_id, "workout", limit=limit)
    return data.get("records", [])


async def get_recovery(user_id: str, limit: int = 5) -> list[dict]:
    data = await list_records(user_id, "recovery", limit=limit)
    return data.get("records", [])


async def get_cycles(user_id: str, limit: int = 5) -> list[dict]:
    data = await list_records(user_id, "cycle", limit=limit)
    return data.get("records", [])


async def get_recovery_for_cycle(user_id: str, cycle_id: int) -> dict:
    token = await get_valid_access_token(user_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/cycle/{cycle_id}/recovery",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def get_sleep_for_cycle(user_id: str, cycle_id: int) -> dict:
    token = await get_valid_access_token(user_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/cycle/{cycle_id}/sleep",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def get_profile(user_id: str) -> dict:
    token = await get_valid_access_token(user_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/user/profile/basic",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def get_body(user_id: str) -> dict:
    token = await get_valid_access_token(user_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/user/measurement/body",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()
