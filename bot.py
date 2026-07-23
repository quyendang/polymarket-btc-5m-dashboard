"""Polymarket BTC 5-minute up/down trading bot — main engine.

Clock-based snipe: every 5 minutes a new market opens at a Unix timestamp
divisible by 300. The bot calculates the active window, sleeps until ~T-10s
before it closes, runs technical analysis in a tight loop, and fires a trade in
the predicted direction (Up/Down).

    python bot.py --dry-run --mode safe          # real data, simulated fills
    python bot.py --dry-run --mode degen --max-trades 20
    python bot.py --dry-run --once
    python bot.py --mode safe                     # LIVE (requires Phase 3 + confirm)

Live execution is implemented in execution.py and gated behind explicit
confirmation; dry-run is the default, safe path.
"""

from __future__ import annotations

import argparse
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from dotenv import load_dotenv

import data
import markets
import pricing
import strategy
from guide import GUIDE

load_dotenv()

# Snipe timing (seconds before window close).
SNIPE_START = GUIDE.snipe_start
HARD_DEADLINE = GUIDE.hard_deadline
TICK_START = GUIDE.tick_start
POLL_INTERVAL = GUIDE.poll_interval
SPIKE_THRESHOLD = GUIDE.spike_threshold
CANDLE_LOOKBACK = GUIDE.candle_lookback


class NoSignalError(RuntimeError):
    """Raised when the bot cannot produce any TA signal for a window."""


class RunCancelled(RuntimeError):
    """Raised when an emergency stop interrupts a pre-trade operation."""


@dataclass(frozen=True)
class RunConfig:
    """Operational controls allowed for CLI and dashboard runs."""

    run_kind: str
    mode: str
    session_budget: float
    min_bet: float
    once: bool = False
    max_trades: Optional[int] = None

    def __post_init__(self) -> None:
        if self.run_kind not in ("dry_run", "live"):
            raise ValueError("run_kind must be dry_run or live")
        if self.mode not in MODES:
            raise ValueError(f"unknown mode: {self.mode}")
        if self.session_budget <= 0:
            raise ValueError("session_budget must be positive")
        if self.min_bet <= 0:
            raise ValueError("min_bet must be positive")
        if self.max_trades is not None and self.max_trades <= 0:
            raise ValueError("max_trades must be positive when provided")

    @property
    def dry_run(self) -> bool:
        return self.run_kind == "dry_run"


@dataclass
class EngineEvent:
    event_type: str
    state: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class CancellationToken:
    """Thread-safe graceful and emergency stop requests."""

    def __init__(self) -> None:
        self._graceful = threading.Event()
        self._emergency = threading.Event()

    def request_stop(self, emergency: bool = False) -> None:
        self._graceful.set()
        if emergency:
            self._emergency.set()

    @property
    def stop_requested(self) -> bool:
        return self._graceful.is_set()

    @property
    def emergency_requested(self) -> bool:
        return self._emergency.is_set()

    def wait(self, seconds: float) -> bool:
        return self._emergency.wait(max(seconds, 0.0))


# --- Trading modes ---------------------------------------------------------

@dataclass(frozen=True)
class Mode:
    name: str
    min_confidence: float
    describe: str


MODES = {
    "safe": Mode("safe", GUIDE.safe_confidence,
                 "25% of bankroll, min 30% confidence"),
    "aggressive": Mode("aggressive", GUIDE.aggressive_confidence,
                       "bet proceeds only, protect original"),
    "degen": Mode("degen", GUIDE.degen_confidence,
                  "all-in every trade, take every signal"),
}


def bet_size(mode: str, bankroll: float, starting: float, min_bet: float,
             has_traded: bool) -> float:
    """Compute the stake for the next trade per the active mode.

    safe:       25% of current bankroll.
    aggressive: first trade risks the original; afterward bet only proceeds
                (bankroll - starting), protecting the principal.
    degen:      entire bankroll, every time.
    """
    if mode == "safe":
        bet = GUIDE.safe_bankroll_fraction * bankroll
    elif mode == "aggressive":
        profits = bankroll - starting
        if not has_traded:
            bet = bankroll                      # first trade risks original
        elif profits >= min_bet:
            bet = profits                       # bet only the proceeds
        else:
            bet = 0.0                           # protect principal; no deployable proceeds
    elif mode == "degen":
        bet = bankroll
    else:
        raise ValueError(f"unknown mode: {mode}")
    bet = max(min(bet, bankroll), 0.0)
    if bet <= 0:
        return 0.0
    # Respect the platform minimum where the bankroll allows it.
    if bet < min_bet:
        bet = min(min_bet, bankroll)
    return round(bet, 4)


# --- Dry-run outcome scoring (pure) ---------------------------------------

@dataclass
class TradeResult:
    window_ts: int
    direction: str
    signal_score: float
    confidence: float
    delta_pct: float
    entry_price: float
    bet: float
    shares: float
    won: bool
    pnl: float
    bankroll_after: float


def score_dry_run(direction: str, bet: float, delta_pct: float,
                  actual_up: bool, entry_override: Optional[float] = None
                  ) -> tuple[float, float, bool, float]:
    """Simulate a fill + resolution.

    Returns (entry_price, shares, won, pnl).
    `entry_override`: real Polymarket ask when available (honest dry-run);
    otherwise the delta-based pricing model is used.
    Winning shares pay $1.00; a loss forfeits the whole stake.
    """
    entry = entry_override if entry_override else pricing.entry_price(delta_pct, direction)
    shares = bet / entry if entry > 0 else 0.0
    won = (direction == "up" and actual_up) or (direction == "down" and not actual_up)
    pnl = shares * (1.0 - entry) if won else -bet
    return entry, shares, won, round(pnl, 6)


def real_ask_price(window_ts: int, direction: str) -> Optional[float]:
    """Best ask (what we'd pay) for the chosen token on live Polymarket.

    Returns None if unavailable so callers fall back to the pricing model.
    """
    try:
        from py_clob_client_v2 import ClobClient
        m = markets.fetch_market(window_ts)
        token = m.token_for(direction)
        if not token:
            return None
        client = ClobClient(
            host=os.getenv("CLOB_HOST", "https://clob.polymarket.com"),
            chain_id=137,
        )
        resp = client.get_price(token, side="BUY")
        price = float(resp.get("price", 0)) if isinstance(resp, dict) else float(resp)
        return price if 0.0 < price < 1.0 else None
    except Exception:
        return None


# --- Snipe loop ------------------------------------------------------------

@dataclass
class SnipeState:
    best: Optional[strategy.Signal] = None
    ticks: list = field(default_factory=list)
    fired_reason: str = ""


def _fetch_context(window_ts: int) -> tuple[list, float, float]:
    """Fetch candles, window-open price, and current spot for analysis."""
    candles = data.get_klines(interval="1m", limit=CANDLE_LOOKBACK)
    open_candle = data.get_candle_at(window_ts)
    window_open = open_candle.open if open_candle else candles[0].open
    current = data.get_ticker_price()
    return candles, window_open, current


def run_snipe(window_ts: int, close_ts: int, mode: Mode,
              verbose: bool = True,
              cancellation: Optional[CancellationToken] = None,
              on_signal: Optional[Callable[[strategy.Signal, float, int], None]] = None,
              ) -> tuple[strategy.Signal, float, str]:
    """Poll ticks, then run the TA loop from T-10s to T-5s.

    Returns (chosen_signal, delta_pct_at_fire, fire_reason).
    Raises NoSignalError if no Binance context was available at all.
    """
    state = SnipeState()

    # Phase A: accumulate 2s ticks leading up to the snipe.
    while time.time() < close_ts - SNIPE_START:
        if cancellation and cancellation.emergency_requested:
            raise RunCancelled("emergency stop before snipe")
        if time.time() >= close_ts - TICK_START:
            try:
                state.ticks.append(data.get_ticker_price())
            except data.BinanceError:
                pass
        if cancellation and cancellation.wait(POLL_INTERVAL):
            raise RunCancelled("emergency stop before snipe")
        if not cancellation:
            time.sleep(POLL_INTERVAL)

    # Phase B: snipe loop T-10s -> T-5s.
    prev_score = None
    delta_at_fire = 0.0
    while time.time() < close_ts - HARD_DEADLINE:
        if cancellation and cancellation.emergency_requested:
            raise RunCancelled("emergency stop during snipe")
        try:
            candles, window_open, current = _fetch_context(window_ts)
        except data.BinanceError:
            if cancellation and cancellation.wait(POLL_INTERVAL):
                raise RunCancelled("emergency stop during snipe")
            if not cancellation:
                time.sleep(POLL_INTERVAL)
            continue
        state.ticks.append(current)
        sig = strategy.analyze(candles, window_open, current, state.ticks)
        delta_pct = (current - window_open) / window_open * 100 if window_open else 0.0

        if state.best is None or abs(sig.score) > abs(state.best.score):
            state.best = sig
            delta_at_fire = delta_pct
        if verbose:
            print(f"    T-{int(close_ts - time.time())}s  {sig}  delta={delta_pct:+.4f}%")
        if on_signal:
            on_signal(sig, delta_pct, max(int(close_ts - time.time()), 0))

        # Spike detection — the market just tipped; fire now.
        if prev_score is not None and abs(sig.score - prev_score) >= SPIKE_THRESHOLD:
            return sig, delta_pct, "spike"
        # Confidence threshold met.
        if sig.confidence >= mode.min_confidence and sig.direction != "flat":
            return sig, delta_pct, "confidence"
        prev_score = sig.score
        if cancellation and cancellation.wait(POLL_INTERVAL):
            raise RunCancelled("emergency stop during snipe")
        if not cancellation:
            time.sleep(POLL_INTERVAL)

    # Phase C: hard deadline — use the best signal we saw. If Binance was
    # unavailable for the whole snipe window, do not invent a default UP trade.
    if state.best is None:
        raise NoSignalError("no Binance context was available during the snipe window")
    return state.best, delta_at_fire, "deadline"


def resolve_window(
    window_ts: int,
    gamma_timeout: float = 60.0,
    gamma_poll_interval: float = 5.0,
) -> Optional[bool]:
    """Did BTC close >= open for this window? True=Up won, False=Down, None=unknown.

    Primary: Binance klines (open of first minute vs close of last minute).
    Fallback: poll Polymarket Gamma resolved outcome/near-$1 prices.
    """
    try:
        open_candle = data.get_candle_at(window_ts)
        last_candle = data.get_candle_at(window_ts + 240)  # last 1m of the window
        if open_candle and last_candle:
            return last_candle.close >= open_candle.open
    except data.BinanceError:
        pass
    # Fallback: poll Gamma resolution/outcome prices.
    deadline = time.time() + max(gamma_timeout, 0.0)
    while True:
        try:
            m = markets.fetch_market(window_ts)
            if m.winner == "Up":
                return True
            if m.winner == "Down":
                return False
        except Exception:
            pass
        if time.time() >= deadline:
            break
        time.sleep(max(gamma_poll_interval, 0.0))
    return None


# --- Main loop -------------------------------------------------------------

def resolve_direction(sig: strategy.Signal, delta_pct: float) -> str:
    """Pick a concrete Up/Down even if the signal is flat (never skip)."""
    if sig.direction in ("up", "down"):
        return sig.direction
    return "up" if delta_pct >= 0 else "down"


class TradingEngine:
    """Shared engine used by the CLI and Railway trader worker."""

    def __init__(
        self,
        config: RunConfig,
        event_sink: Optional[Callable[[EngineEvent], None]] = None,
        cancellation: Optional[CancellationToken] = None,
        executor: Any = None,
        verbose: bool = True,
    ) -> None:
        self.config = config
        self.mode = MODES[config.mode]
        self.event_sink = event_sink
        self.cancellation = cancellation or CancellationToken()
        self.executor = executor
        self.verbose = verbose

    def _emit(self, event_type: str, state: str, message: str, **payload: Any) -> None:
        event = EngineEvent(event_type, state, message, payload)
        if self.event_sink:
            self.event_sink(event)
        if self.verbose and message:
            print(message)

    def _sleep_until(self, target: float, interruptible: bool = True) -> None:
        while time.time() < target:
            remaining = min(target - time.time(), 1.0)
            if interruptible and self.cancellation.wait(remaining):
                raise RunCancelled("emergency stop")
            if not interruptible:
                time.sleep(max(remaining, 0.0))

    def _signal_event(self, sig: strategy.Signal, delta_pct: float, seconds_left: int) -> None:
        self._emit(
            "signal",
            "sniping",
            "",
            score=sig.score,
            confidence=sig.confidence,
            direction=sig.direction,
            breakdown=sig.breakdown,
            delta_pct=delta_pct,
            seconds_left=seconds_left,
        )

    def run(self) -> dict[str, Any]:
        starting = self.config.session_budget
        bankroll = starting
        min_bet = self.config.min_bet
        has_traded = False
        trades = wins = 0

        if not self.config.dry_run and self.executor is None:
            import execution
            self.executor = execution.LiveExecutor(dry_run=False)

        if not self.config.dry_run:
            available = self.executor.usdc_balance()
            if available is not None:
                bankroll = min(bankroll, available)

        banner = "DRY RUN" if self.config.dry_run else "LIVE TRADING"
        self._emit(
            "run_started",
            "starting",
            f"[{banner}] mode={self.mode.name} budget=${bankroll:.2f}",
            guide_profile=GUIDE.profile_id,
            mode=self.mode.name,
            bankroll=bankroll,
            min_bet=min_bet,
        )

        try:
            while True:
                if self.cancellation.stop_requested and trades == 0:
                    break

                window_ts = markets.current_window()
                close_ts = window_ts + markets.WINDOW_SECONDS
                if time.time() > close_ts - SNIPE_START:
                    self._sleep_until(close_ts + 1)
                    continue

                if bankroll < min_bet:
                    if self.config.dry_run:
                        self._emit(
                            "bankroll_reset",
                            "waiting",
                            f"Bankroll ${bankroll:.4f} dưới min bet; reset dry-run.",
                            bankroll=bankroll,
                            reset_to=starting,
                        )
                        bankroll = starting
                        has_traded = False
                    else:
                        self._emit("run_stopped", "stopping", "Số dư khả dụng dưới min bet.")
                        break

                self._emit(
                    "window",
                    "waiting",
                    f"Theo dõi cửa sổ {window_ts}",
                    window_ts=window_ts,
                    close_ts=close_ts,
                    slug=markets.slug_for(window_ts),
                    bankroll=bankroll,
                )

                if not self.config.dry_run:
                    # Fetch Gamma token IDs and balance at T-40 so the T-5
                    # deadline is reserved for signing and posting the order.
                    self._sleep_until(close_ts - TICK_START)
                    try:
                        prepared_balance = self.executor.prepare_window(window_ts)
                        if prepared_balance is not None:
                            bankroll = min(bankroll, prepared_balance)
                    except Exception as exc:
                        self._emit(
                            "no_trade",
                            "waiting",
                            f"Không giao dịch: live preflight thất bại ({exc})",
                            window_ts=window_ts,
                        )
                        if self.config.once:
                            break
                        self._sleep_until(close_ts + 1)
                        continue

                try:
                    sig, delta_pct, reason = run_snipe(
                        window_ts,
                        close_ts,
                        self.mode,
                        verbose=self.verbose,
                        cancellation=self.cancellation,
                        on_signal=self._signal_event,
                    )
                except NoSignalError as exc:
                    self._emit("no_trade", "waiting", f"Không giao dịch: {exc}", window_ts=window_ts)
                    if self.config.once:
                        break
                    continue

                direction = resolve_direction(sig, delta_pct)
                bet = bet_size(self.mode.name, bankroll, starting, min_bet, has_traded)
                if bet <= 0:
                    self._emit(
                        "no_trade",
                        "stopping" if self.mode.name == "aggressive" else "waiting",
                        "Không còn lợi nhuận khả dụng; vốn gốc được bảo vệ.",
                        window_ts=window_ts,
                    )
                    if self.config.once or self.mode.name == "aggressive":
                        break
                    continue

                self._emit(
                    "trade_fired",
                    "placing",
                    f"FIRE [{reason}] {direction.upper()} ${bet:.2f}",
                    window_ts=window_ts,
                    close_ts=close_ts,
                    direction=direction,
                    bet=bet,
                    score=sig.score,
                    confidence=sig.confidence,
                    breakdown=sig.breakdown,
                    delta_pct=delta_pct,
                    fire_reason=reason,
                )

                if self.config.dry_run:
                    real_ask = real_ask_price(window_ts, direction)
                    price_src = "live-ask" if real_ask else "model"
                    self._emit("resolving", "resolving", "Đang chờ kết quả cửa sổ.", window_ts=window_ts)
                    self._sleep_until(close_ts + 1, interruptible=False)
                    actual_up = resolve_window(window_ts)
                    if actual_up is None:
                        self._emit(
                            "trade_unresolved",
                            "waiting",
                            "Không thể xác định kết quả cửa sổ.",
                            window_ts=window_ts,
                        )
                        continue
                    entry, shares, won, pnl = score_dry_run(
                        direction, bet, delta_pct, actual_up, entry_override=real_ask)
                    bankroll += pnl
                    fill_payload = {
                        "order_kind": "simulated",
                        "order_id": None,
                        "entry_price": entry,
                        "shares": shares,
                        "spent": bet,
                        "price_source": price_src,
                    }
                else:
                    fill = self.executor.execute(
                        window_ts,
                        close_ts,
                        direction,
                        bet,
                        cancellation=self.cancellation,
                    )
                    if not fill.ok:
                        self._emit(
                            "trade_failed",
                            "waiting",
                            f"Lệnh không khớp: {fill.detail}",
                            window_ts=window_ts,
                            direction=direction,
                            bet=bet,
                            order_kind=fill.kind,
                            order_id=fill.order_id,
                        )
                        if self.config.once:
                            break
                        continue

                    self._emit("resolving", "resolving", "Lệnh đã khớp; chờ kết quả.", window_ts=window_ts)
                    self._sleep_until(close_ts + 1, interruptible=False)
                    actual_up = resolve_window(window_ts)
                    entry = fill.average_price or (
                        fill.spent / fill.filled_shares if fill.filled_shares else 0.0)
                    shares = fill.filled_shares
                    spent = fill.spent or bet
                    won = actual_up is not None and (
                        (direction == "up" and actual_up) or
                        (direction == "down" and not actual_up)
                    )
                    pnl = shares * (1.0 - entry) if won else (-spent if actual_up is not None else 0.0)
                    available = self.executor.usdc_balance()
                    bankroll = min(starting, available) if available is not None else max(bankroll - spent, 0.0)
                    fill_payload = {
                        "order_kind": fill.kind,
                        "order_id": fill.order_id,
                        "condition_id": fill.condition_id,
                        "token_id": fill.token_id,
                        "entry_price": entry,
                        "shares": shares,
                        "spent": spent,
                        "price_source": "live-fill",
                        "fill_status": fill.status,
                    }

                has_traded = True
                trades += 1
                wins += 1 if won else 0
                outcome = None if actual_up is None else ("up" if actual_up else "down")
                self._emit(
                    "trade_result",
                    "waiting",
                    f"Kết quả {'WIN' if won else 'LOSS'} · PnL {pnl:+.4f}",
                    window_ts=window_ts,
                    slug=markets.slug_for(window_ts),
                    direction=direction,
                    actual_outcome=outcome,
                    won=won,
                    pnl=round(pnl, 6),
                    bankroll_after=round(bankroll, 6),
                    score=sig.score,
                    confidence=sig.confidence,
                    breakdown=sig.breakdown,
                    delta_pct=delta_pct,
                    bet=bet,
                    claim_required=bool(won and not self.config.dry_run),
                    **fill_payload,
                )

                if self.cancellation.stop_requested:
                    break
                if self.config.once or (
                    self.config.max_trades is not None and trades >= self.config.max_trades
                ):
                    break
        except RunCancelled as exc:
            self._emit("run_cancelled", "stopping", str(exc))
        except Exception as exc:
            self._emit("run_failed", "failed", f"Bot dừng do lỗi: {exc}", error=str(exc))
            raise

        summary = {
            "trades": trades,
            "wins": wins,
            "win_rate": wins / trades if trades else 0.0,
            "final_bankroll": round(bankroll, 6),
            "starting_bankroll": starting,
        }
        self._emit("run_completed", "completed", "Phiên bot đã kết thúc.", **summary)
        return summary


def run(mode_name: str, dry_run: bool, once: bool, max_trades: Optional[int]) -> None:
    config = RunConfig(
        run_kind="dry_run" if dry_run else "live",
        mode=mode_name,
        session_budget=float(os.getenv("STARTING_BANKROLL", "1.0")),
        min_bet=float(os.getenv("MIN_BET", "1.0")),
        once=once,
        max_trades=max_trades,
    )
    TradingEngine(config).run()


def main() -> None:
    p = argparse.ArgumentParser(description="Polymarket BTC 5-min up/down bot")
    p.add_argument("--mode", choices=list(MODES), default=os.getenv("BOT_MODE", "safe"))
    p.add_argument("--dry-run", action="store_true", help="real data, no real trades")
    p.add_argument("--once", action="store_true", help="run a single window then exit")
    p.add_argument("--max-trades", type=int, default=None)
    p.add_argument("--i-understand-live", action="store_true",
                   help="required acknowledgement to place REAL trades")
    args = p.parse_args()

    if not args.dry_run and not args.i_understand_live:
        raise SystemExit(
            "Refusing to trade live without --i-understand-live.\n"
            "This bot bets real USDC on a high-variance, likely-negative-EV game.\n"
            "Use --dry-run first, or pass --i-understand-live to proceed live."
        )

    run(args.mode, args.dry_run, args.once, args.max_trades)


if __name__ == "__main__":
    main()
