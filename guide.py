"""Immutable trading rules derived from PolymarketBot.md.

The dashboard may tune operational limits, but live runs must always use this
profile. Keeping the rules in one frozen object prevents the web layer from
silently drifting away from the build guide.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuideProfile:
    profile_id: str = "polymarket-btc-5m-v1"
    window_seconds: int = 300
    tick_start: int = 40
    snipe_start: int = 10
    hard_deadline: int = 5
    poll_interval: float = 2.0
    spike_threshold: float = 1.5
    candle_lookback: int = 30
    confidence_divisor: float = 7.0
    safe_confidence: float = 0.30
    aggressive_confidence: float = 0.20
    degen_confidence: float = 0.0
    safe_bankroll_fraction: float = 0.25
    window_delta_decisive_pct: float = 0.10
    window_delta_strong_pct: float = 0.02
    window_delta_moderate_pct: float = 0.005
    window_delta_slight_pct: float = 0.001
    window_delta_decisive_weight: float = 7.0
    window_delta_strong_weight: float = 5.0
    window_delta_moderate_weight: float = 3.0
    window_delta_slight_weight: float = 1.0
    momentum_weight: float = 2.0
    acceleration_weight: float = 1.5
    ema_weight: float = 1.0
    rsi_weight: float = 2.0
    volume_weight: float = 1.0
    tick_trend_weight: float = 2.0
    ema_short_period: int = 9
    ema_long_period: int = 21
    rsi_period: int = 14
    rsi_overbought: float = 75.0
    rsi_oversold: float = 25.0
    volume_surge_ratio: float = 1.5
    tick_trend_min_ratio: float = 0.60
    tick_trend_min_move_pct: float = 0.005
    fok_retry_interval: float = 3.0
    gtc_limit_price: float = 0.95
    minimum_order_shares: float = 5.0


GUIDE = GuideProfile()


def window_delta_weight(pct: float) -> float:
    """Guide-locked tiered contribution for the dominant indicator."""
    magnitude = abs(pct)
    if magnitude > GUIDE.window_delta_decisive_pct:
        return GUIDE.window_delta_decisive_weight
    if magnitude > GUIDE.window_delta_strong_pct:
        return GUIDE.window_delta_strong_weight
    if magnitude > GUIDE.window_delta_moderate_pct:
        return GUIDE.window_delta_moderate_weight
    if magnitude > GUIDE.window_delta_slight_pct:
        return GUIDE.window_delta_slight_weight
    return 0.0
