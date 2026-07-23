"""Dashboard API, guide lock, and normalized execution tests."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import timedelta
from importlib.metadata import version
from inspect import signature

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
from app.readiness import credentials_complete  # noqa: E402
from execution import Fill, LiveExecutor  # noqa: E402
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
    assert GUIDE.candle_lookback == 30
    assert GUIDE.confidence_divisor == 7.0
    assert (
        GUIDE.safe_confidence,
        GUIDE.aggressive_confidence,
        GUIDE.degen_confidence,
        GUIDE.safe_bankroll_fraction,
    ) == (0.30, 0.20, 0.0, 0.25)
    assert (
        GUIDE.window_delta_decisive_pct,
        GUIDE.window_delta_strong_pct,
        GUIDE.window_delta_moderate_pct,
        GUIDE.window_delta_slight_pct,
    ) == (0.10, 0.02, 0.005, 0.001)
    assert (
        GUIDE.window_delta_decisive_weight,
        GUIDE.window_delta_strong_weight,
        GUIDE.window_delta_moderate_weight,
        GUIDE.window_delta_slight_weight,
    ) == (7.0, 5.0, 3.0, 1.0)
    assert (
        GUIDE.momentum_weight,
        GUIDE.acceleration_weight,
        GUIDE.ema_weight,
        GUIDE.rsi_weight,
        GUIDE.volume_weight,
        GUIDE.tick_trend_weight,
    ) == (2.0, 1.5, 1.0, 2.0, 1.0, 2.0)
    assert (
        GUIDE.ema_short_period,
        GUIDE.ema_long_period,
        GUIDE.rsi_period,
        GUIDE.rsi_overbought,
        GUIDE.rsi_oversold,
    ) == (9, 21, 14, 75.0, 25.0)
    assert (
        GUIDE.volume_surge_ratio,
        GUIDE.tick_trend_min_ratio,
        GUIDE.tick_trend_min_move_pct,
    ) == (1.5, 0.60, 0.005)
    assert (
        GUIDE.fok_retry_interval,
        GUIDE.gtc_limit_price,
        GUIDE.minimum_order_shares,
    ) == (3.0, 0.95, 5.0)


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


def test_clob_v2_readiness_rejects_legacy_proxy_signature(monkeypatch):
    presence = {
        "POLY_PRIVATE_KEY": True,
        "POLY_API_KEY": True,
        "POLY_API_SECRET": True,
        "POLY_API_PASSPHRASE": True,
        "POLY_FUNDER_ADDRESS": True,
    }
    assert credentials_complete(presence, 3) is True

    monkeypatch.setattr(trader_worker, "credential_presence", lambda: presence)
    monkeypatch.setenv("POLY_SIGNATURE_TYPE", "1")
    state = trader_worker.refresh_readiness()
    assert state["credentials_complete"] is False
    assert state["api_valid"] is False
    assert state["signature_type"] == 1


def test_clob_v2_runtime_contract_matches_pinned_client():
    from py_clob_client_v2 import (
        ClobClient,
        MarketOrderArgs,
        MarketOrderArgsV2,
        OrderArgs,
        OrderArgsV2,
        OrderPayload,
        SignatureTypeV2,
    )

    assert version("py-clob-client-v2") == "1.1.0"
    assert MarketOrderArgs is MarketOrderArgsV2
    assert OrderArgs is OrderArgsV2
    assert int(SignatureTypeV2.EOA) == 0
    assert int(SignatureTypeV2.POLY_1271) == 3
    assert list(signature(ClobClient.get_order).parameters) == ["self", "order_id"]
    assert list(signature(ClobClient.cancel_order).parameters) == ["self", "payload"]
    assert list(signature(OrderPayload).parameters) == ["orderID"]


def test_live_client_uses_deposit_wallet_for_signature_type_3(monkeypatch):
    from py_clob_client_v2 import SignatureTypeV2

    signer_key = (
        "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    )
    deposit_wallet = "0x45b31Fc27CC5b7b493e3071E663268cE28090532"
    monkeypatch.setenv("POLY_PRIVATE_KEY", signer_key)
    monkeypatch.setenv("POLY_API_KEY", "api-key")
    monkeypatch.setenv("POLY_API_SECRET", "api-secret")
    monkeypatch.setenv("POLY_API_PASSPHRASE", "api-passphrase")
    monkeypatch.setenv("POLY_SIGNATURE_TYPE", "3")
    monkeypatch.setenv("POLY_FUNDER_ADDRESS", deposit_wallet)

    client = LiveExecutor._build_client()

    assert client.builder.signature_type == SignatureTypeV2.POLY_1271
    assert client.builder.funder == deposit_wallet


def test_live_client_ignores_funder_for_eoa_signature_type(monkeypatch):
    from py_clob_client_v2 import SignatureTypeV2

    signer_key = (
        "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    )
    signer_address = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    monkeypatch.setenv("POLY_PRIVATE_KEY", signer_key)
    monkeypatch.setenv("POLY_API_KEY", "api-key")
    monkeypatch.setenv("POLY_API_SECRET", "api-secret")
    monkeypatch.setenv("POLY_API_PASSPHRASE", "api-passphrase")
    monkeypatch.setenv("POLY_SIGNATURE_TYPE", "0")
    monkeypatch.setenv(
        "POLY_FUNDER_ADDRESS", "0x45b31Fc27CC5b7b493e3071E663268cE28090532")

    client = LiveExecutor._build_client()

    assert client.builder.signature_type == SignatureTypeV2.EOA
    assert client.builder.funder == signer_address


class FakeClient:
    def __init__(self, snapshots: list[dict], response: dict | None = None,
                 order_book: dict | None = None, cancel_response: dict | None = None):
        self.snapshots = snapshots
        self.response = response or {"success": True, "orderID": "order-1"}
        self.order_book = order_book or {"asks": [], "min_order_size": "5"}
        self.cancel_response = cancel_response
        self.cancelled: list[str] = []
        self.market_orders: list[tuple[object, object]] = []
        self.limit_orders: list[tuple[object, object]] = []

    def create_and_post_market_order(self, order_args, order_type):
        self.market_orders.append((order_args, order_type))
        return self.response

    def create_and_post_order(self, order_args, order_type):
        self.limit_orders.append((order_args, order_type))
        return self.response

    def get_order(self, order_id):
        return self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]

    def get_order_book(self, token_id):
        return self.order_book

    def cancel_order(self, payload):
        self.cancelled.append(payload.orderID)
        return self.cancel_response or {"canceled": [payload.orderID], "not_canceled": {}}


class FailingOrderBookClient(FakeClient):
    def get_order_book(self, token_id):
        raise RuntimeError("orderbook unavailable")


def executor_with(client: FakeClient) -> LiveExecutor:
    executor = object.__new__(LiveExecutor)
    executor._client = client
    executor._prepared_markets = {}
    executor._prepared_balances = {}
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
    assert len(fake.market_orders) == 1
    assert fake.market_orders[0][1] == "FOK"
    assert fake.market_orders[0][0].amount == 5.0


@pytest.mark.parametrize("response", [
    {"success": True, "orderID": "fok-1", "status": "live"},
    {"success": True, "status": "matched", "makingAmount": "5.0",
     "takingAmount": "6.25"},
    {"success": True, "orderID": "fok-1", "status": "matched",
     "errorMsg": "no match"},
])
def test_fok_requires_confirmed_matched_order(response):
    fill = executor_with(FakeClient([], response))._try_fok("token", 5.0)

    assert fill.ok is False


def test_clob_client_builds_v2_order_schema(monkeypatch):
    from py_clob_client_v2 import ClobClient, OrderArgs, Side

    # Public Anvil test key used only to verify the local EIP-712 payload shape.
    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    )
    monkeypatch.setattr(client, "get_version", lambda: 2)
    monkeypatch.setattr(client, "get_tick_size", lambda token_id: "0.01")
    monkeypatch.setattr(client, "get_neg_risk", lambda token_id: False)

    signed = client.create_order(OrderArgs(
        token_id="123",
        price=0.5,
        size=10,
        side=Side.BUY,
    ))

    assert signed.timestamp.isdigit()
    assert int(signed.timestamp) > 0
    assert signed.metadata.startswith("0x")
    assert signed.builder.startswith("0x")
    assert not hasattr(signed, "nonce")


def test_clob_client_builds_deposit_wallet_v2_order_schema(monkeypatch):
    from py_clob_client_v2 import (
        ClobClient,
        OrderArgs,
        Side,
        SignatureTypeV2,
    )

    deposit_wallet = "0x45b31Fc27CC5b7b493e3071E663268cE28090532"
    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key="0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
        signature_type=SignatureTypeV2.POLY_1271,
        funder=deposit_wallet,
    )
    monkeypatch.setattr(client, "get_version", lambda: 2)
    monkeypatch.setattr(client, "get_tick_size", lambda token_id: "0.01")
    monkeypatch.setattr(client, "get_neg_risk", lambda token_id: False)

    signed = client.create_order(OrderArgs(
        token_id="123",
        price=0.5,
        size=10,
        side=Side.BUY,
    ))

    assert signed.maker == deposit_wallet
    assert signed.signer == deposit_wallet
    assert signed.signatureType == SignatureTypeV2.POLY_1271
    assert signed.timestamp.isdigit()
    assert signed.metadata.startswith("0x")
    assert signed.builder.startswith("0x")
    assert not hasattr(signed, "nonce")


def test_gtc_waits_for_fill_instead_of_treating_post_as_fill():
    fake = FakeClient([{"status": "matched", "size_matched": "10", "price": "0.95"}])
    fill = executor_with(fake)._try_gtc_fallback("token", 9.5, int(time.time()) + 2)
    assert fill.ok is True
    assert fill.status == "matched"
    assert fill.filled_shares == 10
    assert fill.spent == 9.5
    assert len(fake.limit_orders) == 1
    assert fake.limit_orders[0][1] == "GTC"
    assert fake.limit_orders[0][0].price == GUIDE.gtc_limit_price
    assert fake.limit_orders[0][0].size >= GUIDE.minimum_order_shares


def test_gtc_cancels_unfilled_remainder_at_close():
    fake = FakeClient([{"status": "live", "size_matched": "0", "price": "0.95"}])
    fill = executor_with(fake)._try_gtc_fallback("token", 9.5, int(time.time()))
    assert fill.ok is False
    assert fill.status == "cancelled"
    assert fake.cancelled == ["order-1"]


def test_v2_dict_orderbook_preserves_asks_and_minimum():
    fake = FakeClient([], order_book={
        "asks": [{"price": "0.96", "size": "10"}],
        "min_order_size": "7",
    })
    executor = executor_with(fake)

    assert executor._has_asks("token") is True
    assert executor._min_order_size("token") == 7


def test_orderbook_error_is_not_treated_as_no_asks():
    executor = executor_with(FailingOrderBookClient([]))

    assert executor._has_asks("token") is None
    executor._client = FakeClient([], order_book={"min_order_size": "5"})
    assert executor._has_asks("token") is None


def test_execute_retries_fok_when_orderbook_state_is_unknown(monkeypatch):
    market = type("Market", (), {
        "token_for": lambda self, direction: "token",
    })()
    executor = executor_with(FailingOrderBookClient([]))
    executor._prepared_markets[300] = market
    executor._prepared_balances[300] = 10.0
    attempts = iter([
        Fill(False, "fok", "no match", token_id="token", status="failed"),
        Fill(True, "fok", "matched", order_id="fok-2", token_id="token",
             status="matched", spent=5.0),
    ])
    monkeypatch.setattr(executor, "_try_fok", lambda token_id, usdc: next(attempts))
    monkeypatch.setattr(
        executor,
        "_try_gtc_fallback",
        lambda *args, **kwargs: pytest.fail("unknown orderbook must not trigger GTC"),
    )
    clock = {"now": 100.0}
    monkeypatch.setattr("execution.time.time", lambda: clock["now"])
    monkeypatch.setattr(
        "execution.time.sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )

    fill = executor.execute(300, 110, "up", 5.0)

    assert fill.ok is True
    assert fill.order_id == "fok-2"
    assert clock["now"] == 100.0 + GUIDE.fok_retry_interval


def test_execute_uses_gtc_only_for_confirmed_empty_asks(monkeypatch):
    market = type("Market", (), {
        "token_for": lambda self, direction: "token",
    })()
    executor = executor_with(FakeClient([], order_book={
        "asks": [],
        "min_order_size": "5",
    }))
    executor._prepared_markets[300] = market
    executor._prepared_balances[300] = 10.0
    monkeypatch.setattr(
        executor,
        "_try_fok",
        lambda token_id, usdc: Fill(False, "fok", "no match", token_id=token_id),
    )
    expected = Fill(False, "gtc", "cancelled", token_id="token", status="cancelled")
    monkeypatch.setattr(
        executor,
        "_try_gtc_fallback",
        lambda token_id, usdc, close_ts, cancellation: expected,
    )

    fill = executor.execute(300, int(time.time()) + 5, "up", 5.0)

    assert fill is expected


def test_execute_retries_fok_when_asks_are_present(monkeypatch):
    market = type("Market", (), {
        "token_for": lambda self, direction: "token",
    })()
    executor = executor_with(FakeClient([], order_book={
        "asks": [{"price": "0.96", "size": "10"}],
        "min_order_size": "5",
    }))
    executor._prepared_markets[300] = market
    executor._prepared_balances[300] = 10.0
    attempts = iter([
        Fill(False, "fok", "no match", token_id="token", status="failed"),
        Fill(True, "fok", "matched", order_id="fok-2", token_id="token",
             status="matched", spent=5.0),
    ])
    monkeypatch.setattr(executor, "_try_fok", lambda token_id, usdc: next(attempts))
    monkeypatch.setattr(
        executor,
        "_try_gtc_fallback",
        lambda *args, **kwargs: pytest.fail("asks present must not trigger GTC"),
    )
    clock = {"now": 100.0}
    monkeypatch.setattr("execution.time.time", lambda: clock["now"])
    monkeypatch.setattr(
        "execution.time.sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )

    fill = executor.execute(300, 110, "up", 5.0)

    assert fill.ok is True
    assert fill.order_id == "fok-2"


def test_execute_blocks_duplicate_when_fok_state_is_ambiguous(monkeypatch):
    market = type("Market", (), {
        "token_for": lambda self, direction: "token",
    })()
    executor = executor_with(FakeClient([], order_book={"asks": []}))
    executor._prepared_markets[300] = market
    executor._prepared_balances[300] = 10.0
    monkeypatch.setattr(
        executor,
        "_try_fok",
        lambda token_id, usdc: Fill(
            False,
            "fok",
            "live",
            order_id="fok-live",
            token_id=token_id,
            status="live",
        ),
    )
    monkeypatch.setattr(
        executor,
        "_try_gtc_fallback",
        lambda *args, **kwargs: pytest.fail("ambiguous FOK must not trigger GTC"),
    )

    fill = executor.execute(300, int(time.time()) + 5, "up", 5.0)

    assert fill.ok is False
    assert fill.status == "reconcile_required"
    assert fill.order_id == "fok-live"


def test_gtc_does_not_claim_cancel_when_api_did_not_confirm():
    fake = FakeClient(
        [{"status": "live", "size_matched": "0", "price": "0.95"}],
        cancel_response={
            "canceled": [],
            "not_canceled": {"order-1": "Order not found or already canceled"},
        },
    )
    fill = executor_with(fake)._try_gtc_fallback(
        "token", 9.5, int(time.time()))

    assert fill.ok is False
    assert fill.status == "cancel_failed"
    assert "cancel unconfirmed" in fill.detail


def test_live_executor_prefetches_market_and_balance(monkeypatch):
    market = type("Market", (), {
        "slug": "btc-updown-5m-300",
        "up_token_id": "up-token",
        "down_token_id": "down-token",
        "token_for": lambda self, direction: (
            self.up_token_id if direction == "up" else self.down_token_id),
    })()
    executor = executor_with(FakeClient([]))
    monkeypatch.setattr("execution.markets.fetch_market", lambda window_ts: market)
    monkeypatch.setattr(executor, "usdc_balance", lambda quiet=False: 28.97)

    assert executor.prepare_window(300) == 28.97

    monkeypatch.setattr(
        "execution.markets.fetch_market",
        lambda window_ts: pytest.fail("prepared market should be reused"),
    )
    monkeypatch.setattr(
        executor,
        "_try_fok",
        lambda token_id, usdc: __import__("execution").Fill(
            True, "fok", "matched", token_id=token_id, spent=usdc),
    )
    fill = executor.execute(300, int(time.time()) + 5, "down", 5.0)

    assert fill.ok is True
    assert fill.token_id == "down-token"
    assert 300 not in executor._prepared_markets


def test_live_executor_reports_when_no_fok_was_submitted(monkeypatch):
    executor = executor_with(FakeClient([]))
    monkeypatch.setattr("execution.time.time", lambda: 100.0)

    fill = executor.execute(300, 99, "down", 5.0)

    assert fill.ok is False
    assert fill.detail == "window closed before order preparation"
