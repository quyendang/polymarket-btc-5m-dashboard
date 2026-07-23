"""Railway worker that owns the single trading engine."""

from __future__ import annotations

import threading
import time
from datetime import timedelta

from sqlalchemy import delete, select, text

import bot
from app.config import settings
from app.database import SessionLocal, engine, init_db
from app.models import BotRun, EngineEvent, Trade, WorkerHeartbeat, utcnow
from guide import GUIDE


ROLE = "trader-worker"
RUNNING_STATES = {"starting", "waiting", "sniping", "placing", "resolving", "stopping"}
ADVISORY_LOCK_KEY = 5_300_202_607


def heartbeat(status: str = "idle", detail: dict | None = None) -> None:
    with SessionLocal() as db:
        item = db.get(WorkerHeartbeat, ROLE)
        if item is None:
            item = WorkerHeartbeat(role=ROLE)
            db.add(item)
        item.status = status
        item.detail = detail or {}
        item.last_seen = utcnow()
        db.commit()


def mark_interrupted_runs() -> None:
    with SessionLocal() as db:
        items = db.scalars(select(BotRun).where(BotRun.status.in_(RUNNING_STATES))).all()
        for item in items:
            item.status = "interrupted"
            item.completed_at = utcnow()
            item.error = "Trader worker restarted; live runs are never auto-resumed."
        db.commit()


def cleanup_events() -> None:
    with SessionLocal() as db:
        db.execute(delete(EngineEvent).where(EngineEvent.expires_at < utcnow()))
        db.commit()


def acquire_process_lock():
    connection = engine.connect()
    if engine.dialect.name == "postgresql":
        acquired = bool(connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
        ))
        if not acquired:
            connection.close()
            return None
    return connection


class DatabaseEventSink:
    def __init__(self, run_id: str):
        self.run_id = run_id

    def __call__(self, event: bot.EngineEvent) -> None:
        expires_at = utcnow() + timedelta(days=settings.event_retention_days)
        with SessionLocal() as db:
            run = db.get(BotRun, self.run_id)
            if run is None:
                return
            db.add(EngineEvent(
                run_id=self.run_id,
                event_type=event.event_type,
                state=event.state,
                message=event.message,
                payload=event.payload,
                expires_at=expires_at,
            ))
            if event.state in RUNNING_STATES | {"completed", "failed"}:
                run.status = event.state
            run.heartbeat_at = utcnow()
            if event.event_type == "trade_result":
                payload = event.payload
                claim_required = bool(payload.get("claim_required"))
                db.add(Trade(
                    run_id=self.run_id,
                    window_ts=int(payload["window_ts"]),
                    slug=str(payload["slug"]),
                    direction=str(payload["direction"]),
                    actual_outcome=payload.get("actual_outcome"),
                    won=payload.get("won"),
                    score=float(payload.get("score", 0)),
                    confidence=float(payload.get("confidence", 0)),
                    breakdown=payload.get("breakdown") or {},
                    delta_pct=float(payload.get("delta_pct", 0)),
                    bet=float(payload.get("bet", 0)),
                    entry_price=float(payload.get("entry_price", 0)),
                    shares=float(payload.get("shares", 0)),
                    spent=float(payload.get("spent", 0)),
                    pnl=float(payload.get("pnl", 0)),
                    bankroll_after=float(payload.get("bankroll_after", 0)),
                    order_kind=str(payload.get("order_kind", "unknown")),
                    order_id=payload.get("order_id"),
                    claim_required=claim_required,
                    claim_status="pending" if claim_required else "not_required",
                ))
                run.trades_count += 1
                run.wins_count += 1 if payload.get("won") else 0
                run.final_bankroll = float(payload.get("bankroll_after", 0))
            elif event.event_type == "run_completed":
                run.summary = event.payload
                run.trades_count = int(event.payload.get("trades", run.trades_count))
                run.wins_count = int(event.payload.get("wins", run.wins_count))
                run.final_bankroll = float(event.payload.get("final_bankroll", 0))
                run.completed_at = utcnow()
            elif event.event_type == "run_failed":
                run.error = str(event.payload.get("error") or event.message)
                run.completed_at = utcnow()
            db.commit()


def monitor_run(run_id: str, token: bot.CancellationToken,
                done: threading.Event) -> None:
    while not done.wait(1.0):
        with SessionLocal() as db:
            item = db.get(BotRun, run_id)
            if item is None:
                token.request_stop(emergency=True)
                return
            item.heartbeat_at = utcnow()
            if item.stop_requested_at:
                token.request_stop(emergency=item.emergency_stop)
            worker = db.get(WorkerHeartbeat, ROLE)
            if worker is None:
                worker = WorkerHeartbeat(role=ROLE)
                db.add(worker)
            worker.status = "running"
            worker.detail = {"run_id": run_id, "run_kind": item.run_kind, "state": item.status}
            worker.last_seen = utcnow()
            db.commit()


def claim_next_run() -> BotRun | None:
    with SessionLocal() as db:
        statement = select(BotRun).where(BotRun.status == "queued").order_by(BotRun.created_at)
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        item = db.scalar(statement)
        if item is None:
            return None
        item.status = "starting"
        item.started_at = utcnow()
        item.heartbeat_at = utcnow()
        db.commit()
        db.refresh(item)
        db.expunge(item)
        return item


def run_job(item: BotRun) -> None:
    lock_connection = acquire_process_lock()
    if lock_connection is None:
        with SessionLocal() as db:
            current = db.get(BotRun, item.id)
            if current:
                current.status = "queued"
                db.commit()
        return

    token = bot.CancellationToken()
    done = threading.Event()
    monitor = threading.Thread(target=monitor_run, args=(item.id, token, done), daemon=True)
    monitor.start()
    try:
        config = bot.RunConfig(
            run_kind=item.run_kind,
            mode=item.mode,
            session_budget=item.session_budget,
            min_bet=item.min_bet,
            once=item.once,
            max_trades=item.max_trades,
        )
        engine_runner = bot.TradingEngine(
            config,
            event_sink=DatabaseEventSink(item.id),
            cancellation=token,
            verbose=False,
        )
        summary = engine_runner.run()
        with SessionLocal() as db:
            current = db.get(BotRun, item.id)
            if current:
                current.status = "completed"
                current.summary = summary
                current.completed_at = utcnow()
                db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            current = db.get(BotRun, item.id)
            if current:
                current.status = "failed"
                current.error = str(exc)[:2000]
                current.completed_at = utcnow()
                db.commit()
    finally:
        done.set()
        monitor.join(timeout=2)
        if engine.dialect.name == "postgresql":
            try:
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
                )
            except Exception:
                pass
        lock_connection.close()


def main() -> None:
    init_db()
    mark_interrupted_runs()
    cleanup_events()
    heartbeat("idle", {"guide_profile": GUIDE.profile_id})
    last_cleanup = time.time()
    while True:
        item = claim_next_run()
        if item:
            run_job(item)
            heartbeat("idle", {"last_run_id": item.id})
        else:
            heartbeat("idle", {"guide_profile": GUIDE.profile_id})
            time.sleep(2)
        if time.time() - last_cleanup > 3600:
            cleanup_events()
            last_cleanup = time.time()


if __name__ == "__main__":
    main()
