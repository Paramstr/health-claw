"""Loads .env and exposes all config. Import this before anything else."""

import os
from dotenv import load_dotenv

load_dotenv()

WHOOP_CLIENT_ID = os.environ["WHOOP_CLIENT_ID"]
WHOOP_CLIENT_SECRET = os.environ["WHOOP_CLIENT_SECRET"]
WHOOP_SCOPES = os.environ.get(
    "WHOOP_SCOPES",
    "read:recovery read:cycles read:sleep read:workout read:profile read:body_measurement offline",
)
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://whoop:whoop_dev@localhost:5432/whoop"
)
APP_SECRET = os.environ["APP_SECRET"]
