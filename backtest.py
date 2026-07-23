"""Historical candle fetcher for the backtesting tool (compare_runs.py).

Pages Binance 1-minute klines backwards N hours and returns them in
chronological order. Reuses data.py so backtests and the live bot read prices
through the exact same path.
"""

from __future__ import annotations

import time

import data

_MAX_LIMIT = 1000  # Binance klines cap per request


def fetch_candles(symbol: str = "BTCUSDT", interval: str = "1m",
                  hours: int = 72) -> list[data.Candle]:
    """Fetch `hours` of candles, chronological (oldest first)."""
    now_ms = int(time.time() * 1000)
    span_ms = hours * 3600 * 1000
    start_ms = now_ms - span_ms

    out: list[data.Candle] = []
    cursor = start_ms
    while cursor < now_ms:
        batch = data.get_klines(symbol=symbol, interval=interval,
                                limit=_MAX_LIMIT, start_ms=cursor)
        if not batch:
            break
        out.extend(batch)
        last_open = batch[-1].open_time * 1000
        next_cursor = last_open + 60_000  # advance one minute past the last
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < _MAX_LIMIT:
            break
        time.sleep(0.2)  # be gentle on the rate limit

    # De-dup by open_time and sort.
    seen = {}
    for c in out:
        seen[c.open_time] = c
    return [seen[k] for k in sorted(seen)]


if __name__ == "__main__":
    import sys
    hrs = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    candles = fetch_candles(hours=hrs)
    print(f"Fetched {len(candles)} 1m candles over {hrs}h")
    if candles:
        print(f"  first: {candles[0].open_time} open={candles[0].open:,.2f}")
        print(f"  last:  {candles[-1].open_time} close={candles[-1].close:,.2f}")
