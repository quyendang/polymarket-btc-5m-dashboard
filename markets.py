"""Polymarket market discovery for BTC 5-minute up/down markets.

Markets are found by *constructing* the slug from the clock, not by searching.
Every window starts at a Unix timestamp divisible by 300 (UTC):

    window_ts = now - (now % 300)   # window open
    close_ts  = window_ts + 300     # window close
    slug      = f"btc-updown-5m-{window_ts}"

One call to gamma-api.polymarket.com/events?slug=... returns the event, whose
markets[0] holds the condition id and the CLOB token ids.

Gotcha: Gamma encodes `outcomes`, `outcomePrices`, and `clobTokenIds` as
JSON *strings*, not arrays — they must be json.loads()'d before indexing.
Index 0 == "Up", index 1 == "Down" (positionally aligned across all three).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

from guide import GUIDE

GAMMA_HOST = os.getenv("GAMMA_HOST", "https://gamma-api.polymarket.com")
WINDOW_SECONDS = GUIDE.window_seconds
WINNER_PRICE_THRESHOLD = 0.99

_SESSION = requests.Session()


@dataclass
class Market:
    slug: str
    window_ts: int          # window open (Unix seconds)
    close_ts: int           # window close (Unix seconds)
    condition_id: Optional[str]
    up_token_id: Optional[str]
    down_token_id: Optional[str]
    up_price: Optional[float]
    down_price: Optional[float]
    closed: bool
    resolved: bool
    winner: Optional[str]   # "Up" | "Down" | None (unresolved)

    def token_for(self, direction: str) -> Optional[str]:
        return self.up_token_id if direction.lower() == "up" else self.down_token_id


def window_for(ts: float) -> int:
    """Start of the 5-minute window containing timestamp `ts`."""
    return int(ts) - (int(ts) % WINDOW_SECONDS)


def current_window(now: Optional[float] = None) -> int:
    return window_for(now if now is not None else time.time())


def slug_for(window_ts: int) -> str:
    return f"btc-updown-5m-{window_ts}"


def _parse_json_array(value, default=None):
    """Gamma sends arrays as JSON strings; tolerate both string and list."""
    if value is None:
        return default
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def fetch_event(slug: str) -> Optional[dict]:
    """Return the raw event dict for a slug, or None if not found."""
    resp = _SESSION.get(f"{GAMMA_HOST}/events", params={"slug": slug}, timeout=10)
    resp.raise_for_status()
    events = resp.json()
    if not events:
        return None
    # The query form returns an array; take the first (should be length 1).
    return events[0] if isinstance(events, list) else events


def fetch_market(window_ts: int) -> Market:
    """Build a Market for the given window, enriched with Gamma data if present.

    If the event isn't on Gamma yet (very new window) or the request fails,
    returns a Market with slug/timestamps populated but token ids None.
    """
    slug = slug_for(window_ts)
    base = Market(
        slug=slug,
        window_ts=window_ts,
        close_ts=window_ts + WINDOW_SECONDS,
        condition_id=None,
        up_token_id=None,
        down_token_id=None,
        up_price=None,
        down_price=None,
        closed=False,
        resolved=False,
        winner=None,
    )
    try:
        event = fetch_event(slug)
    except requests.RequestException:
        return base
    if not event:
        return base

    markets = event.get("markets") or []
    if not markets:
        return base
    m = markets[0]

    outcomes = _parse_json_array(m.get("outcomes"), ["Up", "Down"])
    token_ids = _parse_json_array(m.get("clobTokenIds"), [])
    prices = _parse_json_array(m.get("outcomePrices"), [])

    # Map by outcome name so we don't assume ordering blindly.
    up_idx = outcomes.index("Up") if "Up" in outcomes else 0
    down_idx = outcomes.index("Down") if "Down" in outcomes else 1

    def at(seq, idx):
        return seq[idx] if idx < len(seq) else None

    base.condition_id = m.get("conditionId")
    base.up_token_id = at(token_ids, up_idx)
    base.down_token_id = at(token_ids, down_idx)

    up_p = at(prices, up_idx)
    down_p = at(prices, down_idx)
    base.up_price = float(up_p) if up_p is not None else None
    base.down_price = float(down_p) if down_p is not None else None

    base.closed = bool(m.get("closed"))
    status_resolved = str(m.get("umaResolutionStatus", "")).lower() == "resolved"
    base.resolved = status_resolved

    # After resolution the winning outcome's price goes to approximately 1.00.
    # Only infer a winner from prices once Gamma marks the market closed/resolved
    # so a very lopsided live order book is not mistaken for final settlement.
    if base.resolved or base.closed:
        if base.up_price is not None and base.up_price >= WINNER_PRICE_THRESHOLD:
            base.winner = "Up"
        elif base.down_price is not None and base.down_price >= WINNER_PRICE_THRESHOLD:
            base.winner = "Down"
        base.resolved = base.resolved or base.winner is not None

    return base


if __name__ == "__main__":
    # Smoke test: fetch the current live window and print token ids.
    wts = current_window()
    m = fetch_market(wts)
    print(f"slug: {m.slug}")
    print(f"window: {m.window_ts} -> close: {m.close_ts}")
    print(f"condition_id: {m.condition_id}")
    print(f"Up   token: {m.up_token_id}  price={m.up_price}")
    print(f"Down token: {m.down_token_id}  price={m.down_price}")
    print(f"closed={m.closed} resolved={m.resolved} winner={m.winner}")
