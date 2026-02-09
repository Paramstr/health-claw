from sqlalchemy import Column, String, DateTime, Text, BigInteger, Boolean, Float, Integer, func
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Token(Base):
    __tablename__ = "tokens"

    user_id = Column(String, primary_key=True)
    access_token_enc = Column(Text, nullable=False)
    refresh_token_enc = Column(Text, nullable=False)
    scopes = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trace_id = Column(String, unique=True, nullable=False, index=True)
    event_type = Column(String, nullable=False)
    whoop_id = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    hydrated = Column(Boolean, default=False, nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now())


class RawSleep(Base):
    __tablename__ = "raw_sleep"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    payload_json = Column(Text, nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now())


class RawWorkout(Base):
    __tablename__ = "raw_workout"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    payload_json = Column(Text, nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now())


class RawRecovery(Base):
    __tablename__ = "raw_recovery"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    payload_json = Column(Text, nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now())


# ─── Analysis tables ───────────────────────────────────────────────

class Baseline(Base):
    """Rolling 7d/30d baselines per metric per user."""
    __tablename__ = "baselines"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False, index=True)
    metric = Column(String, nullable=False)          # e.g. "hrv", "rhr", "sleep_duration", "strain", "recovery", "spo2"
    window = Column(String, nullable=False)           # "7d" or "30d"
    mean = Column(Float, nullable=False)
    std = Column(Float, nullable=False)
    sample_count = Column(Integer, nullable=False)
    computed_at = Column(DateTime(timezone=True), server_default=func.now())
    window_start = Column(DateTime(timezone=True), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)


class InsightHistory(Base):
    """Stores every generated insight/report."""
    __tablename__ = "insight_history"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)       # morning_brief, post_workout, weekly_digest, alert
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    input_window_start = Column(DateTime(timezone=True))
    input_window_end = Column(DateTime(timezone=True))
    results_json = Column(Text, nullable=False)       # structured JSON payload
    message_text = Column(Text)                       # final Telegram message
    alert_fingerprints = Column(Text)                 # JSON array of fingerprints if any


class AlertState(Base):
    """Deduplicates alerts by fingerprint with cooldown."""
    __tablename__ = "alert_state"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False, index=True)
    fingerprint = Column(String, nullable=False, index=True)
    severity = Column(String, nullable=False)         # info, watch, alert
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_sent = Column(DateTime(timezone=True), server_default=func.now())
    send_count = Column(Integer, nullable=False, default=1)
    resolved = Column(Boolean, default=False, nullable=False)
    detail_json = Column(Text)                        # structured detail
