"""Delta-based token pricing model for dry-run and backtesting.

Market makers see the same BTC window delta we do. The larger the move from the
window open, the more the *winning* token costs. Using a fixed $0.50 token in
backtests produces fake 2x-every-win returns; this piecewise-linear model,
calibrated to observed live Polymarket trading, keeps simulations honest.

Anchor points (abs delta %, winning-token price):
    0.005% -> 0.50   (coin flip)
    0.02%  -> 0.55   (slight lean)
    0.05%  -> 0.65   (moderate edge)
    0.10%  -> 0.80   (strong, being priced in)
    0.15%+ -> 0.95   (nearly certain; capped)
"""

from __future__ import annotations

# (abs_delta_pct, price) anchors, ascending.
_ANCHORS = [
    (0.005, 0.50),
    (0.02, 0.55),
    (0.05, 0.65),
    (0.10, 0.80),
    (0.15, 0.95),
]
_MIN_PRICE = 0.50   # a token can never be worth less than the coin-flip floor
_MAX_PRICE = 0.97   # nearly-certain cap (never assume a free $1.00)
_LOSING_MIN = 0.03  # the losing-side token still has some bid


def winning_token_price(delta_pct: float) -> float:
    """Price of the token in the *direction of the delta*, given the delta %."""
    a = abs(delta_pct)
    if a <= _ANCHORS[0][0]:
        return _MIN_PRICE
    if a >= _ANCHORS[-1][0]:
        return _MAX_PRICE
    for (x0, y0), (x1, y1) in zip(_ANCHORS, _ANCHORS[1:]):
        if x0 <= a <= x1:
            frac = (a - x0) / (x1 - x0)
            return round(y0 + frac * (y1 - y0), 4)
    return _MAX_PRICE


def entry_price(delta_pct: float, bet_direction: str) -> float:
    """Price we'd pay for the token we're actually buying.

    If we bet with the delta, we pay the (expensive) favored price. If we bet
    against it, we buy the cheap side, priced as (1 - favored), floored so it's
    never free.
    """
    favored = winning_token_price(delta_pct)
    delta_dir = "up" if delta_pct >= 0 else "down"
    if bet_direction == delta_dir:
        return favored
    return round(max(1.0 - favored, _LOSING_MIN), 4)
