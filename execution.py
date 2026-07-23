"""Live order execution against the Polymarket CLOB.

Two order strategies (guide §"Order Execution"):

  1. Primary — FOK market buy for the exact USDC amount on the winning token.
     Retried every few seconds until the window closes.
  2. Fallback — GTC limit buy at $0.95 when the winning token has no asks
     (no sell-side liquidity): we *become* the liquidity. Enforces the
     Polymarket minimum of 5 shares / ~$4.75.

Tick size and min order size are read per-market from the API — never hardcoded.
This module is only imported on the live path (bot.py, not --dry-run).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import markets

FALLBACK_LIMIT_PRICE = 0.95
MIN_SHARES = 5              # Polymarket per-order minimum
RETRY_INTERVAL = 3         # seconds between FOK retries


@dataclass
class Fill:
    ok: bool
    kind: str              # "fok" | "gtc" | "none"
    detail: str
    order_id: Optional[str] = None
    token_id: Optional[str] = None
    status: str = "none"
    average_price: float = 0.0
    filled_shares: float = 0.0
    spent: float = 0.0


class LiveExecutor:
    def __init__(self, dry_run: bool = False):
        if dry_run:
            raise ValueError("LiveExecutor is for live trading only")
        self._client = self._build_client()

    @staticmethod
    def _build_client():
        from py_clob_client_v2 import ApiCreds, ClobClient

        host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
        key = os.getenv("POLY_PRIVATE_KEY")
        if not key or key.startswith("0x..."):
            raise SystemExit("POLY_PRIVATE_KEY not set — cannot trade live.")
        api_key = os.getenv("POLY_API_KEY")
        if not api_key:
            raise SystemExit("API creds missing — run setup_creds.py first.")
        signature_type = int(os.getenv("POLY_SIGNATURE_TYPE", "3"))
        funder = os.getenv("POLY_FUNDER_ADDRESS") or None
        if signature_type in (1, 2):
            raise SystemExit(
                "Legacy proxy/Safe makers are not accepted by CLOB V2. "
                "Use the Deposit Wallet flow with POLY_SIGNATURE_TYPE=3."
            )
        if signature_type == 3 and not funder:
            raise SystemExit(
                "POLY_FUNDER_ADDRESS must be the Deposit Wallet address "
                "when POLY_SIGNATURE_TYPE=3."
            )

        creds = ApiCreds(
            api_key=api_key,
            api_secret=os.getenv("POLY_API_SECRET"),
            api_passphrase=os.getenv("POLY_API_PASSPHRASE"),
        )
        client = ClobClient(
            host=host,
            key=key,
            chain_id=137,
            creds=creds,
            signature_type=signature_type,
            funder=funder,
        )
        client.set_api_creds(creds)
        return client

    # --- pre-trade checks --------------------------------------------------

    def usdc_balance(self, quiet: bool = False) -> Optional[float]:
        try:
            from py_clob_client_v2 import AssetType, BalanceAllowanceParams
            resp = self._client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            # Balances are returned in USDC base units (6 decimals).
            bal = float(resp.get("balance", 0)) if isinstance(resp, dict) else float(resp)
            return bal / 1_000_000 if bal > 1000 else bal
        except Exception as e:
            if not quiet:
                print(f"  [warn] balance check failed: {e}")
            return None

    def _min_order_size(self, token_id: str) -> float:
        try:
            ob = self._client.get_order_book(token_id)
            return float(getattr(ob, "min_order_size", MIN_SHARES) or MIN_SHARES)
        except Exception:
            return MIN_SHARES

    def _has_asks(self, token_id: str) -> bool:
        try:
            ob = self._client.get_order_book(token_id)
            return bool(ob.asks)
        except Exception:
            return False

    # --- order placement ---------------------------------------------------

    def execute(self, window_ts: int, close_ts: int, direction: str,
                bet: float, cancellation=None) -> Fill:
        m = markets.fetch_market(window_ts)
        token = m.token_for(direction)
        if not token:
            return Fill(False, "none", "no token id for direction")

        print(f"  [live] target token {token[:16]}… bet=${bet:.2f} dir={direction}")
        bal = self.usdc_balance()
        if bal is not None:
            print(f"  [live] USDC balance ~${bal:.2f}")
            if bal < bet:
                bet = max(bal, 0.0)
                print(f"  [live] trimming bet to available balance ${bet:.2f}")
        if bet <= 0:
            return Fill(False, "none", "no funds")

        # Primary: FOK market buy, retry until the window closes.
        while time.time() < close_ts:
            if cancellation and cancellation.emergency_requested:
                return Fill(False, "none", "emergency stop before order", token_id=token)
            fill = self._try_fok(token, bet)
            if fill.ok:
                return fill
            # If there's simply no sell-side liquidity, switch to the limit fallback.
            if not self._has_asks(token):
                print("  [live] no asks — switching to GTC $0.95 limit fallback")
                return self._try_gtc_fallback(token, bet, close_ts, cancellation)
            if cancellation and cancellation.wait(RETRY_INTERVAL):
                return Fill(False, "none", "emergency stop during FOK retry", token_id=token)
            if not cancellation:
                time.sleep(RETRY_INTERVAL)

        return Fill(False, "none", "window closed before fill", token_id=token)

    @staticmethod
    def _number(source: dict, *keys: str) -> float:
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return 0.0

    @staticmethod
    def _order_id(source: dict) -> Optional[str]:
        for key in ("orderID", "orderId", "order_id", "id"):
            if source.get(key):
                return str(source[key])
        return None

    @staticmethod
    def _safe_detail(value: Any) -> str:
        if isinstance(value, dict):
            allowed = {
                key: value[key]
                for key in (
                    "success", "status", "errorMsg", "message", "orderID",
                    "orderId", "makingAmount", "takingAmount", "size_matched",
                )
                if key in value
            }
            return json.dumps(allowed, ensure_ascii=True, default=str)[:1200]
        return str(value)[:1200]

    def _fill_metrics(self, source: dict, expected_usdc: float) -> tuple[float, float, float]:
        filled = self._number(source, "size_matched", "sizeMatched", "matched_size")
        price = self._number(source, "average_price", "avgPrice", "price")
        spent = self._number(source, "spent", "amount_matched", "amountMatched")

        making = self._number(source, "makingAmount", "making_amount")
        taking = self._number(source, "takingAmount", "taking_amount")
        if making > 0 and taking > 0:
            spent = min(making, taking)
            filled = max(making, taking)
            price = spent / filled if filled else 0.0
        elif filled > 0 and price > 0:
            spent = filled * price
        elif spent > 0 and price > 0:
            filled = spent / price

        if spent <= 0 and filled > 0 and price > 0:
            spent = filled * price
        if spent > expected_usdc * 1.05:
            spent = expected_usdc
            price = spent / filled if filled else price
        return price, filled, spent

    def _snapshot(self, order_id: str, expected_usdc: float) -> tuple[dict, float, float, float]:
        try:
            raw = self._client.get_order(order_id)
            source = raw if isinstance(raw, dict) else {}
            price, filled, spent = self._fill_metrics(source, expected_usdc)
            return source, price, filled, spent
        except Exception:
            return {}, 0.0, 0.0, 0.0

    def _try_fok(self, token_id: str, usdc: float) -> Fill:
        try:
            from py_clob_client_v2 import MarketOrderArgs, OrderType, Side

            args = MarketOrderArgs(
                token_id=token_id,
                amount=round(usdc, 2),     # for BUY, amount = USDC to spend
                side=Side.BUY,
                order_type=OrderType.FOK,
            )
            # The V2 helper refreshes /version, rebuilds, and retries once if the
            # CLOB changes its active order version between signing and posting.
            resp = self._client.create_and_post_market_order(
                order_args=args,
                order_type=OrderType.FOK,
            )
            source = resp if isinstance(resp, dict) else {}
            ok = bool(resp) and source.get("success", True) is not False
            order_id = self._order_id(source)
            price, filled, spent = self._fill_metrics(source, usdc)
            if order_id and filled <= 0:
                snapshot, price, filled, spent = self._snapshot(order_id, usdc)
                source = snapshot or source
            status = str(source.get("status") or ("matched" if ok else "failed")).lower()
            detail = self._safe_detail(source or resp)
            print(f"  [live] FOK: {detail}")
            return Fill(
                ok,
                "fok",
                detail,
                order_id=order_id,
                token_id=token_id,
                status=status,
                average_price=price,
                filled_shares=filled,
                spent=spent or (usdc if ok else 0.0),
            )
        except Exception as e:
            print(f"  [live] FOK failed: {e}")
            return Fill(False, "fok", self._safe_detail(e), token_id=token_id, status="failed")

    def _try_gtc_fallback(self, token_id: str, usdc: float, close_ts: int,
                          cancellation=None) -> Fill:
        try:
            from py_clob_client_v2 import OrderArgs, OrderType, Side

            min_size = max(self._min_order_size(token_id), MIN_SHARES)
            size = max(usdc / FALLBACK_LIMIT_PRICE, min_size)
            cost = size * FALLBACK_LIMIT_PRICE
            if cost > usdc + 1e-9 and usdc < min_size * FALLBACK_LIMIT_PRICE:
                return Fill(False, "none",
                            f"bankroll ${usdc:.2f} < min ${min_size * FALLBACK_LIMIT_PRICE:.2f}")

            args = OrderArgs(
                token_id=token_id,
                price=FALLBACK_LIMIT_PRICE,
                size=round(size, 2),
                side=Side.BUY,
            )
            resp = self._client.create_and_post_order(
                order_args=args,
                order_type=OrderType.GTC,
            )
            source = resp if isinstance(resp, dict) else {}
            posted = bool(resp) and source.get("success", True) is not False
            order_id = self._order_id(source)
            detail = self._safe_detail(source or resp)
            print(f"  [live] GTC posted: {detail}")
            if not posted or not order_id:
                return Fill(False, "gtc", detail, order_id=order_id,
                            token_id=token_id, status="rejected")

            last_price = last_filled = last_spent = 0.0
            last_status = "live"
            while time.time() < close_ts:
                snapshot, price, filled, spent = self._snapshot(order_id, usdc)
                if snapshot:
                    last_price, last_filled, last_spent = price, filled, spent
                    last_status = str(snapshot.get("status") or "live").lower()
                if last_filled >= size - 0.01 or last_status in ("matched", "filled"):
                    return Fill(True, "gtc", self._safe_detail(snapshot), order_id,
                                token_id, "matched", last_price or FALLBACK_LIMIT_PRICE,
                                last_filled or size, last_spent or cost)
                if cancellation and cancellation.emergency_requested:
                    break
                if cancellation and cancellation.wait(1.0):
                    break
                if not cancellation:
                    time.sleep(1.0)

            try:
                self._client.cancel(order_id)
            except Exception as exc:
                print(f"  [live] GTC cancel warning: {exc}")
            snapshot, price, filled, spent = self._snapshot(order_id, usdc)
            if snapshot:
                last_price, last_filled, last_spent = price, filled, spent
            if last_filled > 0:
                return Fill(True, "gtc", self._safe_detail(snapshot), order_id,
                            token_id, "partial_cancelled",
                            last_price or FALLBACK_LIMIT_PRICE, last_filled, last_spent)
            return Fill(False, "gtc", "GTC unfilled and cancelled", order_id,
                        token_id, "cancelled")
        except Exception as e:
            print(f"  [live] GTC fallback failed: {e}")
            return Fill(False, "gtc", self._safe_detail(e), token_id=token_id,
                        status="failed")
