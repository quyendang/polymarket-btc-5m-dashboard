"""FastAPI dashboard service."""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

import data
import markets
from app.backtests import workbook_bytes
from app.config import settings
from app.database import SessionLocal, get_db, init_db
from app.models import AuditLog, BacktestJob, BotRun, EngineEvent, Trade, WorkerHeartbeat, utcnow
from app.schemas import BacktestCreate, LoginRequest, RunCreate
from app.security import (
    check_login_rate_limit,
    clear_login_failures,
    client_ip,
    login_session,
    record_login_failure,
    require_auth,
    require_csrf,
    verify_password,
)
from guide import GUIDE


ACTIVE_STATUSES = {"queued", "starting", "waiting", "sniping", "placing", "resolving", "stopping"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.production:
        from app.security import configured_password_hash

        configured_password_hash()
    yield


app = FastAPI(
    title="Polymarket Bot Dashboard",
    docs_url=None if settings.production else "/docs",
    lifespan=lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="polybot_session",
    max_age=settings.session_max_age,
    same_site="lax",
    https_only=settings.production,
)

def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def run_dict(item: BotRun | None) -> dict | None:
    if item is None:
        return None
    return {
        "id": item.id,
        "run_kind": item.run_kind,
        "mode": item.mode,
        "guide_profile": item.guide_profile,
        "status": item.status,
        "session_budget": item.session_budget,
        "min_bet": item.min_bet,
        "once": item.once,
        "max_trades": item.max_trades,
        "trades_count": item.trades_count,
        "wins_count": item.wins_count,
        "final_bankroll": item.final_bankroll,
        "summary": item.summary,
        "error": item.error,
        "created_at": iso(item.created_at),
        "started_at": iso(item.started_at),
        "completed_at": iso(item.completed_at),
        "heartbeat_at": iso(item.heartbeat_at),
        "stop_requested_at": iso(item.stop_requested_at),
        "emergency_stop": item.emergency_stop,
    }


def trade_dict(item: Trade) -> dict:
    return {
        "id": item.id,
        "run_id": item.run_id,
        "window_ts": item.window_ts,
        "slug": item.slug,
        "direction": item.direction,
        "actual_outcome": item.actual_outcome,
        "won": item.won,
        "score": item.score,
        "confidence": item.confidence,
        "breakdown": item.breakdown,
        "delta_pct": item.delta_pct,
        "bet": item.bet,
        "entry_price": item.entry_price,
        "shares": item.shares,
        "spent": item.spent,
        "pnl": item.pnl,
        "bankroll_after": item.bankroll_after,
        "order_kind": item.order_kind,
        "order_id": item.order_id,
        "claim_required": item.claim_required,
        "claim_status": item.claim_status,
        "market_url": f"https://polymarket.com/event/{item.slug}",
        "created_at": iso(item.created_at),
    }


def event_dict(item: EngineEvent) -> dict:
    return {
        "id": item.id,
        "run_id": item.run_id,
        "event_type": item.event_type,
        "state": item.state,
        "message": item.message,
        "payload": item.payload,
        "created_at": iso(item.created_at),
    }


def backtest_dict(item: BacktestJob, include_results: bool = False) -> dict:
    payload = {
        "id": item.id,
        "status": item.status,
        "hours": item.hours,
        "starting_bankroll": item.starting_bankroll,
        "min_bet": item.min_bet,
        "windows_count": item.windows_count,
        "error": item.error,
        "created_at": iso(item.created_at),
        "started_at": iso(item.started_at),
        "completed_at": iso(item.completed_at),
        "best": (item.results or {}).get("best"),
    }
    if include_results:
        payload["results"] = item.results
    return payload


def audit(db: Session, request: Request, action: str, detail: dict | None = None) -> None:
    db.add(AuditLog(action=action, ip_address=client_ip(request), detail=detail or {}))


def market_snapshot() -> dict:
    now = time.time()
    window_ts = markets.current_window(now)
    result = {
        "server_time": now,
        "window_ts": window_ts,
        "close_ts": window_ts + markets.WINDOW_SECONDS,
        "slug": markets.slug_for(window_ts),
        "btc_price": None,
        "window_open": None,
        "delta_pct": None,
        "up_price": None,
        "down_price": None,
        "market_available": False,
    }
    try:
        current = data.get_ticker_price()
        candle = data.get_candle_at(window_ts)
        result["btc_price"] = current
        if candle:
            result["window_open"] = candle.open
            result["delta_pct"] = (current - candle.open) / candle.open * 100
    except Exception:
        pass
    try:
        market = markets.fetch_market(window_ts)
        result.update({
            "up_price": market.up_price,
            "down_price": market.down_price,
            "market_available": bool(market.up_token_id and market.down_token_id),
        })
    except Exception:
        pass
    return result


@app.get("/health")
def health() -> dict:
    return {"ok": True, "role": "web", "guide_profile": GUIDE.profile_id}


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    ip = client_ip(request)
    check_login_rate_limit(ip)
    if not verify_password(payload.password):
        record_login_failure(ip)
        audit(db, request, "login_failed")
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Mật khẩu không đúng.")
    clear_login_failures(ip)
    csrf = login_session(request)
    audit(db, request, "login_success")
    db.commit()
    return {"authenticated": True, "csrf": csrf}


@app.post("/api/auth/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request, db: Session = Depends(get_db)) -> dict:
    audit(db, request, "logout")
    db.commit()
    request.session.clear()
    return {"ok": True}


@app.get("/api/auth/me", dependencies=[Depends(require_auth)])
def me(request: Request) -> dict:
    return {
        "authenticated": True,
        "csrf": request.session.get("csrf"),
        "session_max_age": settings.session_max_age,
    }


@app.get("/api/dashboard/snapshot", dependencies=[Depends(require_auth)])
def dashboard_snapshot(db: Session = Depends(get_db)) -> dict:
    active = db.scalar(
        select(BotRun).where(BotRun.status.in_(ACTIVE_STATUSES)).order_by(BotRun.created_at.desc())
    )
    latest_run = db.scalar(select(BotRun).order_by(BotRun.created_at.desc()))
    trades = db.scalars(select(Trade).order_by(Trade.created_at.desc()).limit(20)).all()
    events = db.scalars(select(EngineEvent).order_by(EngineEvent.id.desc()).limit(30)).all()
    workers = db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.role)).all()
    totals = db.execute(
        select(func.count(Trade.id), func.coalesce(func.sum(Trade.pnl), 0.0),
               func.coalesce(func.sum(func.cast(Trade.won, Integer)), 0))
    ).one()
    return {
        "guide_profile": GUIDE.profile_id,
        "active_run": run_dict(active),
        "latest_run": run_dict(latest_run),
        "market": market_snapshot(),
        "trades": [trade_dict(item) for item in trades],
        "events": [event_dict(item) for item in events],
        "workers": [
            {"role": item.role, "status": item.status, "detail": item.detail,
             "last_seen": iso(item.last_seen)}
            for item in workers
        ],
        "stats": {"trades": totals[0], "pnl": float(totals[1]), "wins": int(totals[2])},
    }


@app.get("/api/market/candles", dependencies=[Depends(require_auth)])
def market_candles(limit: int = Query(default=60, ge=20, le=240)) -> dict:
    try:
        candles = data.get_klines(interval="1m", limit=limit)
        return {"candles": [
            {"time": item.open_time, "open": item.open, "high": item.high,
             "low": item.low, "close": item.close, "volume": item.volume}
            for item in candles
        ]}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Không lấy được dữ liệu Binance: {exc}") from exc


@app.get("/api/runs", dependencies=[Depends(require_auth)])
def list_runs(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(BotRun).order_by(BotRun.created_at.desc()).limit(limit)).all()
    return {"items": [run_dict(item) for item in items]}


@app.post("/api/runs", dependencies=[Depends(require_csrf)])
def create_run(payload: RunCreate, request: Request, db: Session = Depends(get_db)) -> dict:
    active = db.scalar(select(BotRun).where(BotRun.status.in_(ACTIVE_STATUSES)))
    if active:
        raise HTTPException(status_code=409, detail="Đang có một phiên bot hoạt động.")
    if payload.run_kind == "live":
        if not settings.live_trading_enabled:
            raise HTTPException(status_code=403, detail="Live trading đang bị khóa bởi Railway env.")
        if payload.confirmation_text != "GIAO DICH THAT" or not payload.password:
            raise HTTPException(status_code=422, detail="Thiếu xác nhận live trading.")
        if not verify_password(payload.password):
            raise HTTPException(status_code=401, detail="Mật khẩu xác nhận không đúng.")

    item = BotRun(
        run_kind=payload.run_kind,
        mode=payload.mode,
        guide_profile=GUIDE.profile_id,
        status="queued",
        session_budget=payload.session_budget,
        min_bet=payload.min_bet,
        once=payload.once,
        max_trades=payload.max_trades,
    )
    db.add(item)
    audit(db, request, "run_created", {
        "run_id": item.id, "run_kind": payload.run_kind, "mode": payload.mode,
        "session_budget": payload.session_budget,
    })
    db.commit()
    db.refresh(item)
    return run_dict(item)


@app.post("/api/runs/{run_id}/stop", dependencies=[Depends(require_csrf)])
def stop_run(run_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    item = db.get(BotRun, run_id)
    if not item or item.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên đang chạy.")
    item.stop_requested_at = utcnow()
    if item.status == "queued":
        item.status = "completed"
        item.completed_at = utcnow()
        item.summary = {"trades": 0, "wins": 0, "stopped_before_start": True}
    else:
        item.status = "stopping"
    audit(db, request, "run_stop_requested", {"run_id": run_id})
    db.commit()
    return run_dict(item)


@app.post("/api/runs/{run_id}/emergency-stop", dependencies=[Depends(require_csrf)])
def emergency_stop(run_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    item = db.get(BotRun, run_id)
    if not item or item.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên đang chạy.")
    item.stop_requested_at = utcnow()
    item.emergency_stop = True
    if item.status == "queued":
        item.status = "completed"
        item.completed_at = utcnow()
        item.summary = {"trades": 0, "wins": 0, "stopped_before_start": True}
    else:
        item.status = "stopping"
    audit(db, request, "run_emergency_stop", {"run_id": run_id})
    db.commit()
    return run_dict(item)


@app.get("/api/trades", dependencies=[Depends(require_auth)])
def list_trades(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(Trade).order_by(Trade.created_at.desc()).limit(limit)).all()
    return {"items": [trade_dict(item) for item in items]}


@app.post("/api/trades/{trade_id}/claim-acknowledge", dependencies=[Depends(require_csrf)])
def acknowledge_claim(trade_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    item = db.get(Trade, trade_id)
    if not item or not item.claim_required:
        raise HTTPException(status_code=404, detail="Không có vị thế cần claim.")
    item.claim_status = "acknowledged"
    audit(db, request, "claim_acknowledged", {"trade_id": trade_id})
    db.commit()
    return trade_dict(item)


@app.post("/api/backtests", dependencies=[Depends(require_csrf)])
def create_backtest(payload: BacktestCreate, request: Request,
                    db: Session = Depends(get_db)) -> dict:
    item = BacktestJob(
        status="queued",
        hours=payload.hours,
        starting_bankroll=payload.starting_bankroll,
        min_bet=payload.min_bet,
    )
    db.add(item)
    audit(db, request, "backtest_created", {
        "backtest_id": item.id, "hours": payload.hours,
        "starting_bankroll": payload.starting_bankroll,
    })
    db.commit()
    db.refresh(item)
    return backtest_dict(item)


@app.get("/api/backtests", dependencies=[Depends(require_auth)])
def list_backtests(db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(BacktestJob).order_by(BacktestJob.created_at.desc()).limit(50)).all()
    return {"items": [backtest_dict(item) for item in items]}


@app.get("/api/backtests/{job_id}", dependencies=[Depends(require_auth)])
def get_backtest(job_id: str, db: Session = Depends(get_db)) -> dict:
    item = db.get(BacktestJob, job_id)
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy backtest.")
    return backtest_dict(item, include_results=True)


@app.get("/api/backtests/{job_id}/download", dependencies=[Depends(require_auth)])
def download_backtest(job_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    item = db.get(BacktestJob, job_id)
    if not item or item.status != "completed" or not item.results:
        raise HTTPException(status_code=404, detail="Backtest chưa có kết quả để tải.")
    content = workbook_bytes(item.results)
    headers = {"Content-Disposition": f'attachment; filename="backtest-{job_id}.xlsx"'}
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.get("/api/settings", dependencies=[Depends(require_auth)])
def get_settings_view() -> dict:
    secret_names = [
        "POLY_PRIVATE_KEY", "POLY_API_KEY", "POLY_API_SECRET",
        "POLY_API_PASSPHRASE", "POLY_FUNDER_ADDRESS",
    ]
    return {
        "guide": {
            "profile_id": GUIDE.profile_id,
            "window_seconds": GUIDE.window_seconds,
            "tick_start": GUIDE.tick_start,
            "snipe_start": GUIDE.snipe_start,
            "hard_deadline": GUIDE.hard_deadline,
            "poll_interval": GUIDE.poll_interval,
            "spike_threshold": GUIDE.spike_threshold,
            "mode_confidence": {
                "safe": GUIDE.safe_confidence,
                "aggressive": GUIDE.aggressive_confidence,
                "degen": GUIDE.degen_confidence,
            },
        },
        "live_trading_enabled": settings.live_trading_enabled,
        "environment": {name: bool(os.getenv(name)) for name in secret_names},
        "database": "postgresql" if settings.sqlalchemy_url.startswith("postgresql") else "sqlite",
        "timezone": settings.timezone,
        "password_hash_configured": bool(settings.dashboard_password_hash),
    }


@app.get("/api/system/health", dependencies=[Depends(require_auth)])
def system_health(db: Session = Depends(get_db)) -> dict:
    workers = db.scalars(select(WorkerHeartbeat)).all()
    now = datetime.now(timezone.utc)
    def age_seconds(value: datetime) -> float:
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return (now - normalized).total_seconds()

    return {
        "web": {"status": "healthy", "guide_profile": GUIDE.profile_id},
        "workers": [
            {
                "role": item.role,
                "status": item.status,
                "detail": item.detail,
                "last_seen": iso(item.last_seen),
                "stale": age_seconds(item.last_seen) > 20,
            }
            for item in workers
        ],
    }


@app.get("/api/events", dependencies=[Depends(require_auth)])
async def events(last_id: int = Query(default=0, ge=0)) -> StreamingResponse:
    async def stream():
        cursor = last_id
        while True:
            with SessionLocal() as db:
                items = db.scalars(
                    select(EngineEvent).where(EngineEvent.id > cursor)
                    .order_by(EngineEvent.id).limit(100)
                ).all()
                for item in items:
                    cursor = item.id
                    yield f"id: {item.id}\ndata: {json.dumps(event_dict(item), default=str)}\n\n"
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def frontend(full_path: str) -> Response:
    if not FRONTEND_DIST.exists():
        return Response("Frontend chưa được build. Chạy npm run build trong frontend/.", status_code=503)
    requested = FRONTEND_DIST / full_path
    if full_path and requested.is_file() and FRONTEND_DIST in requested.resolve().parents:
        return FileResponse(requested)
    return FileResponse(FRONTEND_DIST / "index.html")
