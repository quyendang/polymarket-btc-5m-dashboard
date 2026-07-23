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
    momentum_weight: float = 2.0
    acceleration_weight: float = 1.5
    ema_weight: float = 1.0
    rsi_weight: float = 2.0
    volume_weight: float = 1.0
    tick_trend_weight: float = 2.0


GUIDE = GuideProfile()


def window_delta_weight(pct: float) -> float:
    """Guide-locked tiered contribution for the dominant indicator."""
    magnitude = abs(pct)
    if magnitude > 0.10:
        return 7.0
    if magnitude > 0.02:
        return 5.0
    if magnitude > 0.005:
        return 3.0
    if magnitude > 0.001:
        return 1.0
    return 0.0
