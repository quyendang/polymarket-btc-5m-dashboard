"""Tests for the pure dry-run/backtest logic: pricing, bet sizing, scoring."""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot        # noqa: E402
import compare_runs  # noqa: E402
import markets    # noqa: E402
import pricing    # noqa: E402
import strategy   # noqa: E402


# --- pricing model ---------------------------------------------------------

def test_pricing_floor_and_cap():
    assert pricing.winning_token_price(0.0) == 0.50
    assert pricing.winning_token_price(0.001) == 0.50   # below first anchor
    assert pricing.winning_token_price(0.5) == 0.97     # above last anchor -> cap
    assert pricing.winning_token_price(-0.5) == 0.97    # sign-independent


def test_pricing_monotonic():
    xs = [0.001, 0.01, 0.03, 0.07, 0.10, 0.13, 0.2]
    prices = [pricing.winning_token_price(x) for x in xs]
    assert prices == sorted(prices)


def test_pricing_anchor_interpolation():
    # Halfway between 0.02 (0.55) and 0.05 (0.65) is delta 0.035 -> ~0.60
    assert abs(pricing.winning_token_price(0.035) - 0.60) < 0.01


def test_entry_price_with_and_against_delta():
    # Strong up delta: buying Up is expensive, buying Down is cheap.
    up_cost = pricing.entry_price(0.12, "up")
    down_cost = pricing.entry_price(0.12, "down")
    assert up_cost > 0.80
    assert down_cost < 0.20
    assert abs((up_cost + down_cost) - 1.0) < 1e-9 or down_cost == 0.03


# --- bet sizing ------------------------------------------------------------

def test_safe_bets_quarter():
    assert bot.bet_size("safe", 100.0, 100.0, 1.0, True) == 25.0


def test_safe_respects_min_bet():
    # 25% of 2.0 = 0.5 but min_bet is 1.0 -> bet 1.0
    assert bot.bet_size("safe", 2.0, 2.0, 1.0, True) == 1.0


def test_degen_all_in():
    assert bot.bet_size("degen", 37.5, 10.0, 1.0, True) == 37.5


def test_aggressive_first_trade_risks_original():
    assert bot.bet_size("aggressive", 100.0, 100.0, 1.0, has_traded=False) == 100.0


def test_aggressive_bets_only_proceeds():
    # Up to $150 from $100 start -> bet only the $50 profit.
    assert bot.bet_size("aggressive", 150.0, 100.0, 1.0, has_traded=True) == 50.0


def test_aggressive_underwater_stops():
    # Below start, already traded -> do not risk remaining principal.
    assert bot.bet_size("aggressive", 80.0, 100.0, 1.0, has_traded=True) == 0.0


def test_aggressive_at_principal_after_trade_stops():
    assert bot.bet_size("aggressive", 100.0, 100.0, 1.0, has_traded=True) == 0.0


def test_aggressive_small_profit_below_min_bet_stops():
    assert bot.bet_size("aggressive", 100.50, 100.0, 1.0, has_traded=True) == 0.0


def test_bet_never_exceeds_bankroll():
    assert bot.bet_size("degen", 0.5, 1.0, 1.0, True) == 0.5


# --- dry-run scoring -------------------------------------------------------

def test_score_win_pays_out():
    # Bet Up $10 at a mild delta; Up actually wins.
    entry, shares, won, pnl = bot.score_dry_run("up", 10.0, 0.02, actual_up=True)
    assert won is True
    assert shares == 10.0 / entry
    assert pnl > 0


def test_score_loss_forfeits_stake():
    entry, shares, won, pnl = bot.score_dry_run("up", 10.0, 0.02, actual_up=False)
    assert won is False
    assert pnl == -10.0


def test_score_strong_delta_low_margin():
    # Betting with a strong delta wins but nets little (token ~$0.95).
    entry, shares, won, pnl = bot.score_dry_run("up", 100.0, 0.20, actual_up=True)
    assert won is True
    assert entry >= 0.95
    assert 0 < pnl < 6.0     # ~$0.03-0.05/share margin only


def test_resolve_direction_flat_uses_delta():
    flat = __import__("strategy").Signal(0.0, "flat", 0.0)
    assert bot.resolve_direction(flat, 0.03) == "up"
    assert bot.resolve_direction(flat, -0.03) == "down"


def test_run_snipe_without_any_signal_raises():
    with pytest.raises(bot.NoSignalError):
        bot.run_snipe(window_ts=0, close_ts=0, mode=bot.MODES["safe"], verbose=False)


def test_resolve_window_polls_gamma_fallback(monkeypatch):
    def raise_binance_error(*args, **kwargs):
        raise bot.data.BinanceError("binance unavailable")

    monkeypatch.setattr(bot.data, "get_candle_at", raise_binance_error)
    monkeypatch.setattr(bot.markets, "fetch_market",
                        lambda window_ts: SimpleNamespace(winner="Down"))

    assert bot.resolve_window(123, gamma_timeout=0, gamma_poll_interval=0) is False


def test_gamma_winner_accepts_near_one_after_close(monkeypatch):
    event = {
        "markets": [{
            "conditionId": "cond",
            "outcomes": '["Up", "Down"]',
            "clobTokenIds": '["up-token", "down-token"]',
            "outcomePrices": '["0.995", "0.005"]',
            "closed": True,
            "umaResolutionStatus": "",
        }]
    }
    monkeypatch.setattr(markets, "fetch_event", lambda slug: event)

    m = markets.fetch_market(300)

    assert m.winner == "Up"
    assert m.resolved is True


def test_backtest_aggressive_stops_when_proceeds_below_min_bet():
    sig = strategy.Signal(7.0, "up", 1.0)
    ev = compare_runs.WindowEval(300, sig, 0.20, "up", True)

    result = compare_runs.simulate(
        [ev, ev], mode="aggressive", threshold=0.0, starting=100.0, min_bet=100.0)

    assert result.trades == 1
