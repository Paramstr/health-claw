import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from sqlalchemy import select, desc, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db import init_db, get_session, async_session
from api.models import WebhookEvent, RawSleep, RawWorkout, RawRecovery
from api.oauth import build_authorize_url, exchange_code, store_tokens
from api.webhook import verify_signature
from api.whoop_client import fetch_object

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MODEL_MAP = {
    "sleep": RawSleep,
    "workout": RawWorkout,
    "recovery": RawRecovery,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("DB tables ready")
    yield


app = FastAPI(title="whoop-ingest", lifespan=lifespan)


# ---------- hydration (runs as background task) ----------
async def hydrate(event_type: str, whoop_id: str, user_id: str, event_db_id: int):
    kind = event_type.split(".")[0]
    model = MODEL_MAP.get(kind)
    if model is None:
        logger.warning("Unknown event kind %s, skipping hydration", kind)
        return

    try:
        data = await fetch_object(user_id, event_type, whoop_id)

        async with async_session() as session:
            await session.merge(
                model(id=whoop_id, user_id=user_id, payload_json=json.dumps(data))
            )
            await session.execute(
                update(WebhookEvent).where(WebhookEvent.id == event_db_id).values(hydrated=True)
            )
            await session.commit()

        logger.info("Stored %s %s for user %s", kind, whoop_id, user_id)
    except Exception:
        logger.exception("Failed to hydrate %s %s", kind, whoop_id)


# ---------- health ----------
@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ---------- OAuth ----------
def _callback_uri(request: Request) -> str:
    """Build the callback URI using forwarded headers (ngrok) or falling back to request URL."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{scheme}://{host}/whoop/callback"


@app.get("/whoop/login")
async def whoop_login(request: Request):
    redirect_uri = _callback_uri(request)
    logger.info("OAuth redirect_uri: %s", redirect_uri)
    url = build_authorize_url(redirect_uri)
    return RedirectResponse(url)


@app.get("/whoop/callback")
async def whoop_callback(request: Request, code: str = None, error: str = None, error_description: str = None):
    if error:
        logger.error("OAuth error: %s — %s", error, error_description)
        return HTMLResponse(f"<h3>OAuth error</h3><p>{error}: {error_description}</p>", status_code=400)
    if not code:
        return HTMLResponse("<h3>Missing authorization code.</h3>", status_code=400)
    redirect_uri = _callback_uri(request)
    token_data = await exchange_code(code, redirect_uri)
    await store_tokens(token_data)
    return HTMLResponse("<h3>WHOOP connected successfully.</h3><p>You can close this tab.</p>")


# ---------- Webhook ----------
@app.post("/whoop/webhook")
async def whoop_webhook(
    request: Request,
    bg: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    body = await request.body()
    signature = request.headers.get("X-Whoop-Signature", "")
    timestamp = request.headers.get("X-Whoop-Signature-Timestamp", "")

    if not verify_signature(body, signature, timestamp):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)
    trace_id = payload.get("trace_id", "")
    event_type = payload.get("type", "")
    whoop_id = str(payload.get("id", ""))
    user_id = str(payload.get("user_id", ""))

    logger.info("Webhook: %s %s trace=%s", event_type, whoop_id, trace_id)

    # Deduplicate on trace_id, return the row id
    stmt = (
        pg_insert(WebhookEvent)
        .values(
            trace_id=trace_id,
            event_type=event_type,
            whoop_id=whoop_id,
            user_id=user_id,
        )
        .on_conflict_do_nothing(index_elements=["trace_id"])
        .returning(WebhookEvent.id)
    )
    result = await session.execute(stmt)
    row = result.first()
    await session.commit()

    # Only hydrate if this was a new event (not a duplicate)
    if row is not None:
        bg.add_task(hydrate, event_type, whoop_id, user_id, row.id)

    return {"status": "accepted"}


# ---------- Debug ----------
@app.get("/debug/latest")
async def debug_latest(session: AsyncSession = Depends(get_session)):
    results = {}
    for model, name in [
        (RawSleep, "sleep"),
        (RawWorkout, "workout"),
        (RawRecovery, "recovery"),
    ]:
        stmt = select(model).order_by(desc(model.received_at)).limit(3)
        rows = (await session.execute(stmt)).scalars().all()
        results[name] = [
            {"id": r.id, "user_id": r.user_id, "received_at": str(r.received_at)}
            for r in rows
        ]
    return JSONResponse(results)
