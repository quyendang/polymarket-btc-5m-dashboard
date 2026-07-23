# Polymarket BTC 5-Minute Up/Down Bot

Trades Polymarket's "BTC Up or Down" 5-minute binary markets. Every 5 minutes it
computes a 7-indicator technical-analysis signal from live Binance BTC data,
then snipes an Up/Down token seconds before the window closes. Built from
`PolymarketBot.md`.

> ⚠️ **Read this first.** This is a high-variance, near-zero/negative-EV gambling
> strategy. A live dry-run cycle showed the reality: the bot predicted UP with
> 93% confidence, BTC *did* go up — but the real Polymarket ask was **$0.99**, so
> the correct call earned **+1%** while a wrong call loses the entire stake. By
> the time the direction is clear, the market has already priced it in. Use
> `--dry-run`. Never risk money you're not prepared to lose entirely. "Degen mode"
> busts over time by construction.

## Architecture

| File | Purpose |
|------|---------|
| `bot.py` | Main engine — clock timing, snipe loop, modes, bankroll, dry-run scoring |
| `strategy.py` | `analyze()` — composite weighted signal from 7 indicators |
| `pricing.py` | Delta-based token pricing model (backtest + dry-run fallback) |
| `markets.py` | Slug/window math + Gamma event fetch + token-id parsing |
| `data.py` | Binance klines/ticker via `data-api.binance.vision` + retry |
| `backtest.py` | Historical candle fetcher |
| `compare_runs.py` | 27-config backtest matrix → Excel (3 sheets) |
| `execution.py` | Live order engine (FOK market buy + GTC $0.95 fallback) |
| `setup_creds.py` | Derive Polymarket API creds from private key |
| `auto_claim.py` | Experimental Playwright auto-claimer scaffold; verify selectors before relying on it |
| `app/` | FastAPI dashboard, PostgreSQL models, auth, trader/backtest workers |
| `frontend/` | React + TypeScript operations dashboard |
| `tests/` | Unit tests for strategy, pricing, bet sizing, scoring |

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env
```

Fill `.env` (see `.env.example`). For live trading only, derive API creds:

```bash
python setup_creds.py       # paste output into .env
```

> **Notes from build validation**
> - Binance's main API is geo-blocked in the US (HTTP 451); the bot uses
>   `data-api.binance.vision` automatically.
> - `py-clob-client==0.34.5` is archived upstream but verified working for our
>   reads/orders on Python 3.14 during Phase 0.

## Running

```bash
# Dry run — real data, real Polymarket asks, simulated fills (recommended)
python bot.py --dry-run --mode safe
python bot.py --dry-run --once            # one full cycle
python bot.py --dry-run --mode degen --max-trades 20

# Backtest across 27 configs → Excel
python compare_runs.py --hours 72 --output results.xlsx

# Tests
python -m pytest tests/ -q

# LIVE (real USDC) — requires explicit acknowledgement
python bot.py --mode safe --i-understand-live
```

## Web dashboard (local)

The dashboard uses SQLite locally and PostgreSQL on Railway. Live trading is
locked by default. The development password defaults to `admin`; set a real
password before exposing the service.

```bash
# terminal 1: API + built frontend
cd frontend && npm install && npm run build && cd ..
python -m app.entrypoint

# terminal 2: trader worker
SERVICE_ROLE=trader-worker PORT=8001 python -m app.entrypoint

# terminal 3: backtest worker
SERVICE_ROLE=backtest-worker PORT=8002 python -m app.entrypoint
```

Open `http://localhost:8000`. To use the Vite development server instead, run
`npm run dev` in `frontend/` and open `http://localhost:5173`.

Generate the production password hash:

```bash
python -m app.security 'a-long-unique-password'
```

## Railway deployment

Create one Railway project with PostgreSQL and three services connected to the
same GitHub repository. All services use the included `Dockerfile` and
`railway.toml`; only `SERVICE_ROLE` differs:

| Service | `SERVICE_ROLE` | Replicas |
|---------|----------------|----------|
| Web | `web` | 1 |
| Trader | `trader-worker` | **exactly 1** |
| Backtest | `backtest-worker` | 1 |

Share these variables with all three services:

```env
APP_ENV=production
DATABASE_URL=${{Postgres.DATABASE_URL}}
DASHBOARD_PASSWORD_HASH=<argon2 hash>
SESSION_SECRET=<32+ random characters>
LIVE_TRADING_ENABLED=false
TZ=Asia/Ho_Chi_Minh
```

Add `POLY_*`, `BINANCE_BASE`, `CLOB_HOST`, and `GAMMA_HOST` to the trader
service. Keep `LIVE_TRADING_ENABLED=false` through the first production
dry-run. Real runs require both that env switch and an in-dashboard password +
`GIAO DICH THAT` confirmation.

The web UI can change mode, run budget, minimum bet, one-shot, and max trades.
It cannot change strategy weights or the T-40/T-10/T-5 timing profile. Every
run stores the immutable guide ID `polymarket-btc-5m-v1`.

## How it works

- **Timing:** windows start at Unix timestamps divisible by 300; slug is
  `btc-updown-5m-{window_ts}`. The bot sleeps to T-10s, polls 2s ticks, then runs
  a snipe loop (spike detection, confidence threshold, T-5s hard deadline — never
  skips a window).
- **Signal:** window delta dominates (weight 5–7); momentum, acceleration, EMA
  9/21, RSI-14, volume surge, and real-time tick trend contribute.
  `confidence = min(|score|/7, 1)`.
- **Dry-run honesty:** entry price comes from the *real* Polymarket ask at fire
  time when available, falling back to the delta pricing model. Outcomes are
  resolved from Binance klines (Gamma fallback).
- **Backtest caveat:** with 1-minute candles the finest pre-close snapshot is
  T-60s, and it uses the *modeled* price, so backtest ROI is optimistic — treat
  it as directional, and trust the live-ask dry-run for the real edge.

## Modes

| Mode | Bet size | Min confidence |
|------|----------|----------------|
| safe | 25% of bankroll | 30% |
| aggressive | first trade risks starting bankroll, then proceeds only | 20% |
| degen | all-in | 0% |

Keep `STARTING_BANKROLL >= 4 * MIN_BET` if you want safe mode to behave as a
true 25% stake. With smaller bankrolls, the platform minimum dominates.
