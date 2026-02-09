"""Catch-up script: hydrates any webhook events that were missed.

Normally hydration happens inline via FastAPI BackgroundTasks.
Run this only if events got stuck (hydrated=False):

    python -m worker.hydrate
"""

import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update
from api.db import async_session, init_db
from api.models import WebhookEvent, RawSleep, RawWorkout, RawRecovery
from api.whoop_client import fetch_object

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

MODEL_MAP = {"sleep": RawSleep, "workout": RawWorkout, "recovery": RawRecovery}


async def main():
    await init_db()

    async with async_session() as session:
        stmt = (
            select(WebhookEvent)
            .where(WebhookEvent.hydrated == False)  # noqa: E712
            .order_by(WebhookEvent.id)
        )
        events = (await session.execute(stmt)).scalars().all()

    logger.info("Found %d unhydrated events", len(events))

    for event in events:
        kind = event.event_type.split(".")[0]
        model = MODEL_MAP.get(kind)
        if model is None:
            logger.warning("Unknown kind %s, skipping", kind)
            continue

        try:
            data = await fetch_object(event.user_id, event.event_type, event.whoop_id)
            async with async_session() as session:
                await session.merge(
                    model(id=event.whoop_id, user_id=event.user_id, payload_json=json.dumps(data))
                )
                await session.execute(
                    update(WebhookEvent).where(WebhookEvent.id == event.id).values(hydrated=True)
                )
                await session.commit()
            logger.info("Hydrated %s %s", kind, event.whoop_id)
        except Exception:
            logger.exception("Failed %s %s", kind, event.whoop_id)

    logger.info("Done")


if __name__ == "__main__":
    asyncio.run(main())
