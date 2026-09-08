# options-trading-bot

A systematic options trading system designed to be direction-agnostic — harvesting the
variance risk premium in calm and rising markets, rotating short-delta in declines, and
carrying a permanent income-financed tail hedge for crashes.

**Status: planning, with one sleeve built and running in paper.**

## Start here

🤖 **[docs/AGENT.md](docs/AGENT.md)** — the scheduled 0DTE iron condor agent. Built,
tested, paper only. A short-lived process wakes on a schedule, runs nine gates over
today's chain, trades or stands down, and journals every session either way. This is
the operational half of the project working end to end while the research half is
still being built.

🔬 **[docs/RESEARCH.md](docs/RESEARCH.md)** — the daily improvement loop: stored chains,
competing variants, a locked holdout, and the rules for when a parameter may change.
Built to resist its own conclusions, because at one trade a day the default outcome of
"keep tuning until it looks good" is a strategy fitted to noise.

📄 **[docs/PLAN.md](docs/PLAN.md)** — the full development plan.

Covers:
- Whether Alpaca alone is sufficient (short answer: fine for execution, not for research)
- Data vendors and a phased spend, sized for a small account (~$700–1,000 to go live)
- Strategy design across five sleeves, and the regime engine that switches between them
- Specific indicators, support/resistance methods, screening criteria
- Risk management and circuit breakers
- Backtesting validation protocol
- Phased build plan (~6–8 months to first live capital)

## Target profile

Taxable margin account, under $25k, paper trading until an edge is demonstrated.
See [§2](docs/PLAN.md#2-your-account-profile-and-what-it-constrains) for what that
constrains.

## Stack

Python 3.12 · Polars · DuckDB + Parquet · `alpaca-py` · `py_vollib` · Streamlit · Docker.
Three programs sharing one strategy library: a nightly **loader**, an on-demand
**backtester**, and a daily **trader**. See [§4](docs/PLAN.md#4-what-were-actually-building).

## What runs today

```bash
cd agent && python -m venv .venv && .venv/bin/pip install -e '.[broker,dev]'
.venv/bin/pytest                                          # 168 tests, no keys needed
.venv/bin/trading-agent --config config/agent.yaml demo   # a full synthetic session
```

Run `simulate`, then `replay` and `report`, to watch the whole research loop operate on
synthetic days without credentials.

The agent is one sleeve, not the five in the plan, and its edge is **not proven** — a
short condor is risk-neutral-fair by construction, so everything rests on the variance
risk premium surviving at 0DTE, which the backtest in [§6](docs/PLAN.md#6-backtesting-and-validation)
has not yet tested. Treat its paper P&L as a plumbing test.

## Open questions

Capital amount, margin vs cash account, and max acceptable drawdown — see
[§2](docs/PLAN.md#still-open--i-need-these-to-proceed).

## Disclaimer

Not investment advice. Systematic options trading carries substantial risk of loss.
Nothing in this repository is a projection or guarantee of returns.
