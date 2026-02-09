from sqlalchemy import Column, String, DateTime, Text, BigInteger, Boolean, func
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
