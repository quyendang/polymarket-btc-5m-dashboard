"""Dashboard API, guide lock, and normalized execution tests."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import timedelta

import pytest


TEST_DB = os.path.join(tempfile.gettempdir(), f"polybot-dashboard-{os.getpid()}.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["APP_ENV"] = "development"
os.environ["DASHBOARD_DEV_PASSWORD"] = "admin"
os.environ["LIVE_TRADING_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import WorkerHeartbeat, utcnow  # noqa: E402
from app import trader_worker  # noqa: E402
from execution import LiveExecutor  # noqa: E402
from guide import GUIDE  # noqa: E402


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def authenticate(client: TestClient) -> dict:
    response = client.post("/api/auth/login", json={"password": "admin"})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf"]}


def test_guide_profile_is_locked():
    assert GUIDE.profile_id == "polymarket-btc-5m-v1"
    assert (GUIDE.window_seconds, GUIDE.tick_start, GUIDE.snipe_start, GUIDE.hard_deadline) == (
        300, 40, 10, 5,
    )
    assert GUIDE.poll_interval == 2.0
    assert GUIDE.spike_threshold == 1.5


def test_login_csrf_and_dry_run_creation(client: TestClient):
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    headers = authenticate(client)

    payload = {
        "run_kind": "dry_run",
        "mode": "safe",
        "session_budget": 20,
        "min_bet": 1,
        "once": True,
    }
    assert client.post("/api/runs", json=payload).status_code == 403
    response = client.post("/api/runs", headers=headers, json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["guide_profile"] == GUIDE.profile_id
    assert body["status"] == "queued"


def test_live_run_is_blocked_by_environment(client: TestClient):
    headers = authenticate(client)
    response = client.post("/api/runs", headers=headers, json={
        "run_kind": "live",
        "mode": "safe",
        "session_budget": 20,
        "min_bet": 1,
        "once": True,
        "password": "admin",
        "confirmation_text": "GIAO DICH THAT",
    })
    assert response.status_code == 403
    assert "Railway env" in response.json()["detail"]


def test_settings_use_trader_readiness_without_reading_web_secrets(
    client: TestClient, monkeypatch,
):
    monkeypatch.setenv("POLY_PRIVATE_KEY", "super-secret")
    with SessionLocal() as db:
        db.add(WorkerHeartbeat(
            role="trader-worker",
            status="idle",
            last_seen=utcnow(),
            detail={
                "readiness": {
                    "credentials": {
                        "POLY_PRIVATE_KEY": False,
                        "POLY_API_KEY": False,
                        "POLY_API_SECRET": False,
                        "POLY_API_PASSPHRASE": False,
                        "POLY_FUNDER_ADDRESS": False,
                    },
                    "credentials_complete": False,
                    "api_valid": False,
                    "usdc_balance": None,
                }
            },
        ))
        db.commit()
    authenticate(client)
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["environment"]["POLY_PRIVATE_KEY"] is False
    assert body["trader_readiness"]["worker_online"] is True
    assert "super-secret" not in response.text


def test_live_run_requires_fresh_ready_trader(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "live_trading_enabled", True)
    headers = authenticate(client)
    payload = {
        "run_kind": "live",
        "mode": "safe",
        "session_budget": 20,
        "min_bet": 1,
        "once": True,
        "password": "admin",
        "confirmation_text": "GIAO DICH THAT",
    }

    missing = client.post("/api/runs", headers=headers, json=payload)
    assert missing.status_code == 503
    assert "trader-worker" in missing.json()["detail"]

    with SessionLocal() as db:
        db.add(WorkerHeartbeat(
            role="trader-worker",
            status="idle",
            last_seen=utcnow() - timedelta(seconds=60),
            detail={"readiness": {"credentials_complete": True, "api_valid": True,
                                  "live_trading_enabled": True}},
        ))
        db.commit()
    stale = client.post("/api/runs", headers=headers, json=payload)
    assert stale.status_code == 503
    assert "heartbeat" in stale.json()["detail"]

    with SessionLocal() as db:
        worker = db.get(WorkerHeartbeat, "trader-worker")
        worker.last_seen = utcnow()
        worker.detail = {
            "readiness": {
                "credentials_complete": True,
                "api_valid": True,
                "live_trading_enabled": True,
                "usdc_balance": 29.0,
            }
        }
        db.commit()
    ready = client.post("/api/runs", headers=headers, json=payload)
    assert ready.status_code == 200
    assert ready.json()["run_kind"] == "live"


def test_trader_worker_rechecks_live_readiness_before_engine(monkeypatch):
    blocked = {
        "live_trading_enabled": True,
        "credentials_complete": True,
        "api_valid": False,
        "usdc_balance": None,
    }
    monkeypatch.setattr(trader_worker, "refresh_readiness", lambda: blocked)
    with pytest.raises(RuntimeError, match="preflight failed"):
        trader_worker.assert_live_ready()

    ready = {**blocked, "api_valid": True, "usdc_balance": 29.0}
    monkeypatch.setattr(trader_worker, "refresh_readiness", lambda: ready)
    trader_worker.assert_live_ready()


class FakeClient:
    def __init__(self, snapshots: list[dict], response: dict | None = None):
        self.snapshots = snapshots
        self.response = response or {"success": True, "orderID": "order-1"}
        self.cancelled: list[str] = []

    def create_order(self, args):
        return args

    def create_market_order(self, args):
        return args

    def post_order(self, signed, order_type):
        return self.response

    def get_order(self, order_id):
        return self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]

    def cancel(self, order_id):
        self.cancelled.append(order_id)


def executor_with(client: FakeClient) -> LiveExecutor:
    executor = object.__new__(LiveExecutor)
    executor._client = client
    executor._min_order_size = lambda token: 5
    return executor


def test_fok_normalizes_amounts_without_exposing_raw_response():
    fake = FakeClient([], {
        "success": True,
        "orderID": "fok-1",
        "status": "matched",
        "makingAmount": "5.0",
        "takingAmount": "6.25",
        "private": "must-not-leak",
    })
    fill = executor_with(fake)._try_fok("token", 5.0)
    assert fill.ok is True
    assert fill.order_id == "fok-1"
    assert fill.spent == 5.0
    assert fill.filled_shares == 6.25
    assert fill.average_price == 0.8
    assert "must-not-leak" not in fill.detail


def test_gtc_waits_for_fill_instead_of_treating_post_as_fill():
    fake = FakeClient([{"status": "matched", "size_matched": "10", "price": "0.95"}])
    fill = executor_with(fake)._try_gtc_fallback("token", 9.5, int(time.time()) + 2)
    assert fill.ok is True
    assert fill.status == "matched"
    assert fill.filled_shares == 10
    assert fill.spent == 9.5


def test_gtc_cancels_unfilled_remainder_at_close():
    fake = FakeClient([{"status": "live", "size_matched": "0", "price": "0.95"}])
    fill = executor_with(fake)._try_gtc_fallback("token", 9.5, int(time.time()))
    assert fill.ok is False
    assert fill.status == "cancelled"
    assert fake.cancelled == ["order-1"]
