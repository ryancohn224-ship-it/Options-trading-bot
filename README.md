# options-trading-bot

A systematic options trading system designed to be direction-agnostic — harvesting the
variance risk premium in calm and rising markets, rotating short-delta in declines, and
carrying a permanent income-financed tail hedge for crashes.

**Status: planning. No code yet.**

## Start here

📄 **[docs/PLAN.md](docs/PLAN.md)** — the full development plan.

Covers:
- Whether Alpaca alone is sufficient (short answer: fine for execution, not for research)
- Exact data vendors and cost tiers
- Strategy design across five sleeves, and the regime engine that switches between them
- Specific indicators, support/resistance methods, screening criteria
- Risk management and circuit breakers
- Backtesting validation protocol
- Phased build plan (~6–8 months to first live capital)

## Open questions

See [§2 of the plan](docs/PLAN.md#2-what-i-need-from-you) — capital, account type, risk
tolerance, and data budget are blocking.

## Disclaimer

Not investment advice. Systematic options trading carries substantial risk of loss.
Nothing in this repository is a projection or guarantee of returns.
