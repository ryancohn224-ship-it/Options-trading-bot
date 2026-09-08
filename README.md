# options-trading-bot (otb)

A systematic, defined-risk options trading system. Direction-agnostic by construction: it harvests the
variance risk premium where implied vol exceeds a HAR-IV forecast of realized vol, tilts by skew and trend,
sizes the *aggregate* risk budget by Moreira–Muir volatility targeting, and runs every candidate through a
greek-constrained portfolio selector and a pessimistic cost model before anything is sent to the broker.

Backtest and live trading share one code path (`otb.engine.strategy.decide`). Alpaca is the broker.

- 📄 [docs/PLAN.md](docs/PLAN.md) — the original plan (data, account constraints, risk framework)
- 🔬 [docs/REDESIGN.md](docs/REDESIGN.md) — the research behind this design, with sources
- 🏃 [docs/RUNBOOK.md](docs/RUNBOOK.md) — **how to run it against your Alpaca paper account**

## Status

| Piece | State |
|---|---|
| Pricing (BS, greeks, IV), OCC symbols | ✅ tested |
| Warehouse (Parquet + DuckDB), point-in-time filtering | ✅ tested |
| Synthetic stochastic-vol market with skewed surface and true VRP | ✅ |
| Features: Yang-Zhang RV, HAR-IV forecaster, surface summary, HMM regime | ✅ tested |
| Factors: VRP (F1), skew (F2), trend overlay (F7), execution cost | ✅ |
| Structures: put/call credit spreads, iron condors, width ladder; screener | ✅ tested |
| Portfolio: vol-target scalar, greek/BP/concentration-constrained selector | ✅ tested |
| Risk: drawdown ladder, kill switch, consecutive-loss pause, day-trade guard | ✅ tested |
| Sim broker with 40%-into-spread fill model and expiry settlement | ✅ tested |
| Alpaca broker: multi-leg limit orders with limit walker, ledger, reconciliation | ✅ validated against alpaca-py request models with a fake client; **not yet run against the live API** (sandbox cannot reach Alpaca) |
| Live runner, loader, CLI (`synth/backtest/load/trade/status/doctor`) | ✅ |
| Streamlit dashboard, Dockerfile + cron, CI | ✅ |
| Meta-labeling (F-ML), F3 term structure, F4 dispersion, F5 earnings | ⏳ per REDESIGN §6: after a trade log exists |

## Synthetic-world result (pipeline validation, not a performance claim)

760-day synthetic market with a 3-vol-point VRP, $25k, pessimistic fills:

| | Profit factor | Win rate | Trades |
|---|---|---|---|
| Vol-targeting **on** | **1.58** | 76% | 41 |
| Vol-targeting **off** | 0.81 | 65% | 31 |

Same data, same everything else. That is the direction the literature predicts; the magnitude on real data is
what paper trading is for.

## Quick start

```bash
pip install -e ".[dashboard,dev]" && pytest -q
otb synth --days 760 && otb backtest --warmup 120      # offline, no keys
export APCA_API_KEY_ID=... APCA_API_SECRET_KEY=...
otb doctor && otb load && otb trade --dry-run           # then: otb trade
```

## Disclaimer

Not investment advice. Systematic options trading carries substantial risk of loss. Nothing here is a
projection or guarantee of returns.
