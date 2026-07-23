"""Composite weighted trading signal for BTC 5-minute up/down markets.

`analyze()` is pure and side-effect-free: given candles, the window-open price,
the current price, and accumulated real-time ticks, it returns a single score.
Positive score => bet Up, negative => bet Down.

The seven indicators and their weights are tuned for the 5-minute binary
question. The Window Delta dominates (weight 5-7): it directly answers what the
market asks ("is BTC up or down vs the window open?"). Long-horizon indicators
(EMA, RSI) are noisy at this scale and are deliberately down-weighted.

Confidence divides by 7 (not 10) because the window delta alone can reach 7 —
we don't need every indicator to agree to be confident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from guide import GUIDE, window_delta_weight


@dataclass
class Signal:
    score: float
    direction: str                 # "up" | "down" | "flat"
    confidence: float              # 0.0 - 1.0
    breakdown: dict = field(default_factory=dict)  # per-indicator contributions

    def __str__(self) -> str:
        parts = ", ".join(f"{k}={v:+.2f}" for k, v in self.breakdown.items() if v)
        return (
            f"{self.direction.upper():4s} score={self.score:+.2f} "
            f"conf={self.confidence:.0%} [{parts}]"
        )


# --- Indicator helpers (no third-party TA deps) ---------------------------


def ema(values: list[float], period: int) -> Optional[float]:
    """Exponential moving average of the last `period`-weighted values."""
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Wilder-style RSI over `period`. Returns None if not enough data."""
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    # Smooth across any remaining candles.
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        # No downward movement. If there were no gains either (flat price),
        # RSI is undefined -> treat as neutral. Otherwise pure uptrend -> 100.
        return 50.0 if avg_gain == 0 else 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _window_delta_weight(pct: float) -> float:
    """Tiered weight for the dominant window-delta indicator (magnitude only)."""
    return window_delta_weight(pct)


def _tick_trend(ticks: list[float]) -> float:
    """Direction from accumulated 2s price polls.

    Requires >=60% directional consistency AND >0.005% net move to trigger.
    Returns +weight (up), -weight (down), or 0.
    """
    if len(ticks) < 3:
        return 0.0
    ups = downs = 0
    for i in range(1, len(ticks)):
        if ticks[i] > ticks[i - 1]:
            ups += 1
        elif ticks[i] < ticks[i - 1]:
            downs += 1
    total = ups + downs
    if total == 0:
        return 0.0
    net_pct = (ticks[-1] - ticks[0]) / ticks[0] * 100
    if abs(net_pct) <= 0.005:
        return 0.0
    up_ratio = ups / total
    down_ratio = downs / total
    if net_pct > 0 and up_ratio >= 0.60:
        return GUIDE.tick_trend_weight
    if net_pct < 0 and down_ratio >= 0.60:
        return -GUIDE.tick_trend_weight
    return 0.0


# --- Main entry point ------------------------------------------------------


def analyze(
    candles: list,
    window_open_price: float,
    current_price: float,
    ticks: Optional[list[float]] = None,
) -> Signal:
    """Produce a composite Signal.

    `candles`: chronological list of objects/tuples exposing .close and .volume
        (e.g. data.Candle). Newest last. Needs ~20+ for EMA/RSI to activate.
    `window_open_price`: BTC price at the start of the current 5-min window.
    `current_price`: latest BTC price (spot).
    `ticks`: optional list of the bot's own 2s price polls this window.
    """
    ticks = ticks or []
    closes = [c.close for c in candles]
    volumes = [getattr(c, "volume", 0.0) for c in candles]
    bd: dict = {}
    score = 0.0

    # 1. Window Delta (dominant, weight 5-7 tiered) ------------------------
    if window_open_price:
        window_pct = (current_price - window_open_price) / window_open_price * 100
        w = _window_delta_weight(window_pct)
        contrib = w if window_pct > 0 else -w
        bd["window_delta"] = contrib
        score += contrib
    else:
        window_pct = 0.0

    # 2. Micro Momentum (weight 2) — last 2 candles direction --------------
    if len(closes) >= 3:
        recent = closes[-1] - closes[-3]
        if recent > 0:
            bd["momentum"] = GUIDE.momentum_weight
        elif recent < 0:
            bd["momentum"] = -GUIDE.momentum_weight
        score += bd.get("momentum", 0.0)

    # 3. Acceleration (weight 1.5) — is the move building or fading? --------
    if len(closes) >= 5:
        latest_move = closes[-1] - closes[-2]
        prior_move = closes[-3] - closes[-4]
        if latest_move > 0 and latest_move > prior_move:      # accelerating up
            bd["acceleration"] = GUIDE.acceleration_weight
        elif latest_move < 0 and latest_move < prior_move:    # accelerating down
            bd["acceleration"] = -GUIDE.acceleration_weight
        score += bd.get("acceleration", 0.0)

    # 4. EMA 9/21 crossover (weight 1) -------------------------------------
    ema9 = ema(closes[-30:], 9) if len(closes) >= 9 else None
    ema21 = ema(closes[-30:], 21) if len(closes) >= 21 else None
    if ema9 is not None and ema21 is not None and ema9 != ema21:
        bd["ema"] = GUIDE.ema_weight if ema9 > ema21 else -GUIDE.ema_weight
        score += bd["ema"]

    # 5. RSI-14 extremes (weight 2) — overbought/oversold ------------------
    r = rsi(closes)
    if r is not None:
        if r > 75:
            bd["rsi"] = -GUIDE.rsi_weight   # overbought -> expect pullback (down)
        elif r < 25:
            bd["rsi"] = GUIDE.rsi_weight    # oversold -> expect bounce (up)
        score += bd.get("rsi", 0.0)

    # 6. Volume Surge (weight 1) — confirms current direction --------------
    if len(volumes) >= 6:
        recent_vol = sum(volumes[-3:]) / 3
        prior_vol = sum(volumes[-6:-3]) / 3
        if prior_vol > 0 and recent_vol >= 1.5 * prior_vol:
            # Confirm whichever way price is currently leaning.
            lean = GUIDE.volume_weight if (closes[-1] - closes[-3]) >= 0 else -GUIDE.volume_weight
            bd["volume"] = lean
            score += lean

    # 7. Real-Time Tick Trend (weight 2) -----------------------------------
    tt = _tick_trend(ticks)
    if tt:
        bd["tick_trend"] = tt
        score += tt

    # --- Resolve direction + confidence -----------------------------------
    if score > 0:
        direction = "up"
    elif score < 0:
        direction = "down"
    else:
        direction = "flat"
    confidence = min(abs(score) / GUIDE.confidence_divisor, 1.0)

    return Signal(score=round(score, 4), direction=direction,
                  confidence=confidence, breakdown=bd)
