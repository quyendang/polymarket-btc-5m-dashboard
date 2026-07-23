"""Unit tests for strategy.analyze() and its indicator helpers.

Run: ./venv/bin/python -m pytest tests/ -q
"""

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import strategy  # noqa: E402


@dataclass
class C:
    """Minimal candle stub exposing .close and .volume."""
    close: float
    volume: float = 1.0


def flat_candles(n=25, price=65000.0, vol=1.0):
    return [C(price, vol) for _ in range(n)]


def rising_candles(n=25, start=65000.0, step=10.0, vol=1.0):
    return [C(start + i * step, vol) for i in range(n)]


def falling_candles(n=25, start=65000.0, step=10.0, vol=1.0):
    return [C(start - i * step, vol) for i in range(n)]


# --- Window delta dominance ------------------------------------------------

def test_window_delta_up_dominates():
    # BTC up 0.12% from open, but candles are flat -> should still say UP.
    candles = flat_candles(price=65000.0)
    sig = strategy.analyze(candles, window_open_price=65000.0, current_price=65078.0)
    assert sig.direction == "up"
    assert sig.breakdown["window_delta"] == 7.0


def test_window_delta_down_dominates():
    candles = flat_candles(price=65000.0)
    sig = strategy.analyze(candles, window_open_price=65000.0, current_price=64922.0)
    assert sig.direction == "down"
    assert sig.breakdown["window_delta"] == -7.0


def test_window_delta_overrides_noisy_momentum():
    # Strong window delta up (+7) must beat rising-vs... contrary short signals.
    # Falling candles give negative momentum/accel/ema, but +0.12% delta wins.
    candles = falling_candles()
    sig = strategy.analyze(candles, window_open_price=candles[0].close,
                           current_price=candles[0].close * 1.0012)
    assert sig.direction == "up", sig.breakdown


def test_window_delta_tiers():
    base = 65000.0
    assert strategy._window_delta_weight(0.15) == 7.0
    assert strategy._window_delta_weight(0.05) == 5.0
    assert strategy._window_delta_weight(0.008) == 3.0
    assert strategy._window_delta_weight(0.002) == 1.0
    assert strategy._window_delta_weight(0.0005) == 0.0


# --- Flat / neutral --------------------------------------------------------

def test_flat_is_flat():
    candles = flat_candles(price=65000.0)
    sig = strategy.analyze(candles, window_open_price=65000.0, current_price=65000.0)
    assert sig.direction == "flat"
    assert sig.score == 0.0
    assert sig.confidence == 0.0


# --- Confidence ------------------------------------------------------------

def test_confidence_capped_and_scaled():
    candles = flat_candles(price=65000.0)
    # Big delta plus aligned indicators can exceed 7 -> confidence caps at 1.0.
    sig = strategy.analyze(rising_candles(), window_open_price=rising_candles()[0].close,
                           current_price=rising_candles()[0].close * 1.0015)
    assert 0.0 <= sig.confidence <= 1.0
    # window delta alone (7) -> confidence 1.0
    sig2 = strategy.analyze(candles, 65000.0, 65078.0)
    assert sig2.confidence == 1.0


# --- Tick trend ------------------------------------------------------------

def test_tick_trend_up():
    ticks = [65000, 65005, 65010, 65020, 65030]  # consistent up, >0.005%
    assert strategy._tick_trend(ticks) == 2.0


def test_tick_trend_down():
    ticks = [65030, 65020, 65010, 65005, 65000]
    assert strategy._tick_trend(ticks) == -2.0


def test_tick_trend_choppy_no_signal():
    ticks = [65000, 65010, 65000, 65010, 65000]  # net ~0
    assert strategy._tick_trend(ticks) == 0.0


def test_tick_trend_too_small_move():
    ticks = [65000, 65000.5, 65001, 65001.5]  # <0.005% net
    assert strategy._tick_trend(ticks) == 0.0


# --- Indicator math --------------------------------------------------------

def test_rsi_all_gains_is_100():
    closes = [65000 + i for i in range(20)]
    assert strategy.rsi(closes) == 100.0


def test_rsi_none_when_insufficient():
    assert strategy.rsi([1, 2, 3]) is None


def test_ema_none_when_insufficient():
    assert strategy.ema([1, 2], 9) is None


def test_ema_of_constant_is_constant():
    assert strategy.ema([5.0] * 30, 9) == 5.0
