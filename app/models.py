"""Persistent dashboard state."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid4() -> str:
    return str(uuid.uuid4())


class BotRun(Base):
    __tablename__ = "bot_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    run_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    guide_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    session_budget: Mapped[float] = mapped_column(Float, nullable=False)
    min_bet: Mapped[float] = mapped_column(Float, nullable=False)
    once: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_trades: Mapped[int | None] = mapped_column(Integer)
    trades_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wins_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    final_bankroll: Mapped[float | None] = mapped_column(Float)
    summary: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    emergency_stop: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    trades: Mapped[list["Trade"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    run_id: Mapped[str] = mapped_column(ForeignKey("bot_runs.id", ondelete="CASCADE"), index=True)
    window_ts: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(96), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    actual_outcome: Mapped[str | None] = mapped_column(String(8))
    won: Mapped[bool | None] = mapped_column(Boolean)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    breakdown: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    delta_pct: Mapped[float] = mapped_column(Float, nullable=False)
    bet: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    shares: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    spent: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    bankroll_after: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    order_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(128))
    claim_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    claim_status: Mapped[str] = mapped_column(String(24), nullable=False, default="not_required")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[BotRun] = relationship(back_populates="trades")


class EngineEvent(Base):
    __tablename__ = "engine_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("bot_runs.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class BacktestJob(Base):
    __tablename__ = "backtest_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    hours: Mapped[int] = mapped_column(Integer, nullable=False)
    starting_bankroll: Mapped[float] = mapped_column(Float, nullable=False)
    min_bet: Mapped[float] = mapped_column(Float, nullable=False)
    windows_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    results: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="starting")
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


Index("ix_bot_runs_active", BotRun.status, BotRun.created_at)
Index("ix_trades_run_window", Trade.run_id, Trade.window_ts)
