# options-trading-bot

A systematic options trading system designed to be direction-agnostic — harvesting the
variance risk premium in calm and rising markets, rotating short-delta in declines, and
carrying a permanent income-financed tail hedge for crashes.

**Status: planning. No code yet.**

## Start here

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

## Open questions

Capital amount, margin vs cash account, and max acceptable drawdown — see
[§2](docs/PLAN.md#still-open--i-need-these-to-proceed).

## Disclaimer

Not investment advice. Systematic options trading carries substantial risk of loss.
Nothing in this repository is a projection or guarantee of returns.
