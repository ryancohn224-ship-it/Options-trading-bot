# Runbook — running the bot against Alpaca paper

## Where it has to run

**Not in the Claude Code cloud sandbox.** That environment's egress policy blocks `*.alpaca.markets`
(verified: the proxy returns 403 on CONNECT). Run it on your own machine or a small VPS. Everything below
assumes a shell with Python 3.11+ (or Docker).

## 1. Keys

Alpaca dashboard → Paper Trading → API keys. Then, in the shell that runs the bot:

```bash
export APCA_API_KEY_ID=...
export APCA_API_SECRET_KEY=...
export APCA_PAPER=true
```

Never commit these. `.env` is git-ignored if you prefer a file (`set -a; . ./.env; set +a`).

## 2. Install and check

```bash
git clone https://github.com/ryancohn224-ship-it/Options-trading-bot && cd Options-trading-bot
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dashboard,dev]"
pytest -q                 # 23 tests; all should pass offline
otb doctor                # credentials, trading API, options approval level, chain data
```

`otb doctor` will tell you if the account's options approval is below Level 3 — multi-leg orders
are rejected without it. Apply in the Alpaca dashboard.

**Paper account sizing.** Reset the paper account to **$25,000** in the Alpaca dashboard before starting.
The default config's risk limits are percentages, so it runs at any size, but validating at $100k proves
out a system you can't deploy (see `docs/PLAN.md` §2).

## 3. First runs (do these in order)

```bash
otb load                  # bars for the universe + today's option chains → ./warehouse
otb trade --dry-run       # full decision cycle, prints what it *would* open/close, submits nothing
otb trade                 # real paper orders (market hours only; --force to override the clock check)
otb status                # account, open positions, risk state, ledger-vs-broker reconciliation
```

A dry run during market hours is the right first check. Read `opened`/`closed`/`notes` in the output.
`notes` includes `screened out: {...}` reasons and any risk-state messages.

## 4. Daily operation

The bot is designed to make one decision per day. Two cron entries (times in ET):

```
45 10 * * 1-5  otb trade    # loads fresh data, manages exits, opens new positions
30 16 * * 1-5  otb load     # end-of-day snapshot into the warehouse (feeds tomorrow's features)
```

Or with Docker, which installs exactly that schedule:

```bash
docker build -t otb .
docker run -d --name otb --restart unless-stopped \
  -e APCA_API_KEY_ID -e APCA_API_SECRET_KEY -e APCA_PAPER=true \
  -v $PWD/warehouse:/app/warehouse -v $PWD/state:/app/state otb
docker run -d --name otb-dash -p 8501:8501 -v $PWD/state:/app/state otb dashboard   # http://localhost:8501
```

## 5. What to watch

- **`otb status` → `reconcile`** must be `[]`. Any discrepancy between the ledger and Alpaca's positions halts
  new entries automatically; fix by hand (close the stray leg in the dashboard, or edit `state/positions.json`).
- **`state/risk.json`** — `killed: true` means the −20% kill switch fired. It will not restart itself.
  Delete the flag only after you understand why.
- **Slippage ledger** (dashboard → "Slippage vs mid"). The backtest assumes fills 40% of the half-spread away
  from mid. If live slippage is consistently worse, the backtest is optimistic and sizing should come down.
- **Vol-target scalar** — should fall when the market gets volatile. If it's pinned at 1.0, the HAR forecast
  isn't running (needs ~60 trading days of bars; `otb load` backfills 800 days on first run).

## 6. Backtesting

The warehouse accumulates a real chain snapshot every day the loader runs. After a few months:

```bash
otb backtest --cash 25000 --warmup 60                    # on accumulated real snapshots
otb backtest --cash 25000 --warmup 60 --no-voltarget     # the comparison that matters
otb synth --days 760 && otb backtest --warmup 120        # synthetic world, for pipeline checks only
```

Alpaca's option history begins Feb 2024 and is bars-only (no historical greeks/quotes), so a deep real
backtest still needs ThetaData per `docs/PLAN.md` §1. The loader's snapshot mode is what you run today.

## 7. Files

| Path | What |
|---|---|
| `config/default.yaml` | every parameter; its hash is logged on each trade |
| `warehouse/` | Parquet: option_chains/, equity_bars/, events/ |
| `state/positions.json` | the bot's own position ledger (source of truth for reconciliation) |
| `state/risk.json` | high-water mark, day/month anchors, kill switch, day-trade count |
| `state/surface_history.parquet` | daily IV/skew history (feeds HAR-IV and skew percentile) |
| `state/ledger/` | trades, equity curve, slippage, events |
