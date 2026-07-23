"""Binance market-data access (read-only).

The bot only reads BTC prices/candles from Binance; it never trades there.
Binance's main host (api.binance.com) returns HTTP 451 on US networks, so we
default to the official public market-data mirror `data-api.binance.vision`,
which serves identical /api/v3/* endpoints with no auth.

All functions retry with backoff on transient failures (429 rate limit,
timeouts, 5xx) instead of crashing — the bot polls this every ~2s during the
snipe loop and must survive the occasional hiccup.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

BINANCE_BASE = os.getenv("BINANCE_BASE", "https://data-api.binance.vision")
# Non-US fallback if the mirror is unreachable.
BINANCE_FALLBACK = "https://api.binance.com"

_SESSION = requests.Session()
_MAX_RETRIES = 4
_BACKOFF_BASE = 0.75  # seconds; grows 0.75, 1.5, 3.0, ...


@dataclass
class Candle:
    """One kline. Times are Unix seconds; prices/volume are floats."""

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int

    @classmethod
    def from_row(cls, row: list) -> "Candle":
        # Binance kline array layout:
        # [0]=open time(ms) [1]=open [2]=high [3]=low [4]=close [5]=volume
        # [6]=close time(ms) [7]=quote vol [8]=trades ...
        return cls(
            open_time=int(row[0]) // 1000,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            close_time=int(row[6]) // 1000,
        )


class BinanceError(RuntimeError):
    pass


def _get(path: str, params: dict) -> object:
    """GET a Binance endpoint with retry/backoff, trying mirror then fallback."""
    last_exc: Optional[Exception] = None
    for base in (BINANCE_BASE, BINANCE_FALLBACK):
        for attempt in range(_MAX_RETRIES):
            try:
                resp = _SESSION.get(f"{base}{path}", params=params, timeout=10)
                if resp.status_code == 429:
                    # Rate limited — respect Retry-After if present, else back off.
                    wait = float(resp.headers.get("Retry-After", _BACKOFF_BASE * (2**attempt)))
                    time.sleep(wait)
                    continue
                if resp.status_code == 451:
                    # Geo-blocked on this host; break to try the next base.
                    last_exc = BinanceError(f"451 geo-blocked at {base}")
                    break
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                time.sleep(_BACKOFF_BASE * (2**attempt))
    raise BinanceError(f"Binance request failed: {path} params={params}: {last_exc}")


def get_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    limit: int = 2,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
) -> list[Candle]:
    """Fetch klines. limit max is 1000. start_ms/end_ms are inclusive (ms)."""
    params: dict = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    if end_ms is not None:
        params["endTime"] = end_ms
    rows = _get("/api/v3/klines", params)
    return [Candle.from_row(r) for r in rows]


def get_ticker_price(symbol: str = "BTCUSDT") -> float:
    """Current spot price for a symbol."""
    data = _get("/api/v3/ticker/price", {"symbol": symbol})
    return float(data["price"])


def get_candle_at(window_ts: int, symbol: str = "BTCUSDT") -> Optional[Candle]:
    """Return the 1m candle whose open time == window_ts (Unix seconds), or None."""
    candles = get_klines(symbol=symbol, interval="1m", limit=1, start_ms=window_ts * 1000)
    for c in candles:
        if c.open_time == window_ts:
            return c
    return candles[0] if candles else None


if __name__ == "__main__":
    # Smoke test: prove we can reach Binance and parse candles.
    print(f"Base: {BINANCE_BASE}")
    print(f"Spot BTCUSDT: {get_ticker_price():,.2f}")
    for c in get_klines(limit=2):
        print(f"  {c.open_time} open={c.open:,.2f} close={c.close:,.2f} vol={c.volume:.3f}")
