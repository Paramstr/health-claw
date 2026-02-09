import datetime
import logging
import secrets
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet

from api.config import WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET, WHOOP_SCOPES, APP_SECRET
from api.db import async_session
from api.models import Token

logger = logging.getLogger(__name__)

WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"

_fernet = Fernet(APP_SECRET.encode())


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet.decrypt(value.encode()).decode()


def build_authorize_url(redirect_uri: str) -> str:
    params = {
        "client_id": WHOOP_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": WHOOP_SCOPES,
        "state": secrets.token_urlsafe(32),
    }
    return f"{WHOOP_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str, redirect_uri: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            WHOOP_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": WHOOP_CLIENT_ID,
                "client_secret": WHOOP_CLIENT_SECRET,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            WHOOP_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": WHOOP_CLIENT_ID,
                "client_secret": WHOOP_CLIENT_SECRET,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def fetch_user_id(access_token: str) -> str:
    """Call WHOOP profile endpoint to get the user_id."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.prod.whoop.com/developer/v1/user/profile/basic",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return str(resp.json()["user_id"])


async def store_tokens(token_data: dict):
    user_id = token_data.get("user_id")
    if not user_id:
        user_id = await fetch_user_id(token_data["access_token"])
    user_id = str(user_id)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=token_data["expires_in"]
    )

    async with async_session() as session:
        existing = await session.get(Token, user_id)
        if existing:
            existing.access_token_enc = encrypt(token_data["access_token"])
            existing.refresh_token_enc = encrypt(token_data["refresh_token"])
            existing.scopes = WHOOP_SCOPES
            existing.expires_at = expires_at
        else:
            session.add(
                Token(
                    user_id=user_id,
                    access_token_enc=encrypt(token_data["access_token"]),
                    refresh_token_enc=encrypt(token_data["refresh_token"]),
                    scopes=WHOOP_SCOPES,
                    expires_at=expires_at,
                )
            )
        await session.commit()
    logger.info("Stored tokens for user %s", user_id)


async def get_valid_access_token(user_id: str) -> str:
    async with async_session() as session:
        token = await session.get(Token, user_id)
        if token is None:
            raise ValueError(f"No token for user {user_id}")

        now = datetime.datetime.now(datetime.timezone.utc)
        if token.expires_at > now:
            return decrypt(token.access_token_enc)

        logger.info("Refreshing token for user %s", user_id)
        refresh_tok = decrypt(token.refresh_token_enc)
        new_data = await refresh_access_token(refresh_tok)
        new_data.setdefault("user_id", user_id)
        await store_tokens(new_data)
        return new_data["access_token"]
