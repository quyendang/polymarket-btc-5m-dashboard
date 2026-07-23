"""Isolated backtest worker so historical analysis cannot delay live snipes."""

from __future__ import annotations

import time

from sqlalchemy import select

from app.backtests import execute_backtest
from app.database import SessionLocal, engine, init_db
from app.models import BacktestJob, WorkerHeartbeat, utcnow


ROLE = "backtest-worker"


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


def claim_next() -> BacktestJob | None:
    with SessionLocal() as db:
        statement = select(BacktestJob).where(BacktestJob.status == "queued").order_by(BacktestJob.created_at)
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        item = db.scalar(statement)
        if not item:
            return None
        item.status = "running"
        item.started_at = utcnow()
        db.commit()
        db.refresh(item)
        db.expunge(item)
        return item


def run_job(item: BacktestJob) -> None:
    heartbeat("running", {"backtest_id": item.id, "hours": item.hours})
    try:
        results = execute_backtest(item.hours, item.starting_bankroll, item.min_bet)
        with SessionLocal() as db:
            current = db.get(BacktestJob, item.id)
            if current:
                current.status = "completed"
                current.results = results
                current.windows_count = int(results.get("windows_count", 0))
                current.completed_at = utcnow()
                db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            current = db.get(BacktestJob, item.id)
            if current:
                current.status = "failed"
                current.error = str(exc)[:2000]
                current.completed_at = utcnow()
                db.commit()
    finally:
        heartbeat("idle", {"last_backtest_id": item.id})


def main() -> None:
    init_db()
    heartbeat()
    while True:
        item = claim_next()
        if item:
            run_job(item)
        else:
            heartbeat()
            time.sleep(3)


if __name__ == "__main__":
    main()
