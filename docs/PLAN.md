# Options Trading Bot — Development Plan

**Status:** Planning / pre-build
**Owner:** Ryan Cohn
**Last updated:** 2026-08-01

---

## 0. Read this first

Two things I want on the record before the plan, because they shape every decision below.

**A bot that "trades every day" is a design smell, not a goal.** The single most reliable
way to turn a positive-expectancy strategy into a negative one is to force it to trade when
its criteria aren't met. This plan is built so the bot *can* trade most days — the universe
is wide enough and the sleeves are varied enough that something usually qualifies — but the
bot must be willing to sit flat. Days-with-no-trade is a metric we track, not a bug we fix.

**"Makes money up and down" does not come from predicting direction.** Nobody does that
reliably. It comes from two structural things, and this plan is built on both:

1. **Harvesting the variance risk premium** — implied volatility is, on average, priced
   above subsequently realized volatility. That premium is direction-agnostic. It's the
   closest thing to a durable retail-accessible edge in options.
2. **Owning convexity** — a permanent, small, income-financed long-tail position so that
   the crash that kills most premium sellers pays *us* instead.

The realistic goal is a systematic, risk-controlled program with a modest edge and a
survivable drawdown profile. Not a money printer. If the backtest says money printer, the
backtest is wrong, and Section 6 is about finding out why.

---

## 1. Direct answer: is Alpaca enough?

**Short version: Alpaca is enough to *trade*. It is not enough to *research*.**

| Job | Alpaca? | Verdict |
|---|---|---|
| Brokerage / order execution | ✅ | Yes — use it. Level 3 multi-leg, $0 commission, solid REST + streaming API, real paper trading |
| Real-time options data (live trading) | ✅ | Yes — needs **Algo Trader Plus, $99/mo** for the OPRA feed. The free "indicative" feed is not good enough to trade on |
| Historical options data (backtesting) | ❌ | **No. This is the blocker.** Alpaca's options history only starts **February 2024** |
| Charting for *you* to review | ❌ | No real charting. Not needed by the bot, needed by you |
| Charting/indicators for *the bot* | n/a | Bot computes its own indicators in code. You never need a charting product for this |
| News | ⚠️ | Has a Benzinga-powered news API. Adequate to start, not great |

### Why the Feb 2024 limit is disqualifying on its own

You asked for a strategy that works when the market goes down. Alpaca's options history
covers roughly Feb 2024 → today. That window contains a strong bull market, the Aug 2024
yen-carry vol spike, and the Apr 2025 tariff selloff. It does **not** contain Q4 2018,
COVID March 2020, or the 2022 bear market.

Backtesting a "works in down markets" strategy on a window with no real bear market is not
a test. Any short-premium strategy will look brilliant. **This is exactly how people blow
up.** We need history that includes at least two genuine regime breaks.

### What I recommend you buy

**Tier 2 is my recommendation.** Tier 1 is not sufficient for the strategy you described.

**Tier 1 — Minimum (~$99/mo).** Alpaca Algo Trader Plus only. Viable *only* for
short-lookback intraday sleeves. You cannot validate the core strategy. Not recommended.

**Tier 2 — Recommended (~$200–300/mo).**
- **Alpaca** — execution, $0 options commissions, paper trading — free
- **Alpaca Algo Trader Plus** — live OPRA feed — **$99/mo**
- **ThetaData** — historical options: NBBO quotes, trades, greeks, IV, deep history — **~$80–160/mo**. This is the single most important purchase in the whole plan. It's what makes the backtest real
- **TradingView** — for *you* to eyeball setups and sanity-check the bot — **~$15–30/mo**
- **Free and genuinely good:** FRED (macro), CBOE index data (VIX, VIX9D, VIX3M, SKEW, put/call ratios), Treasury yield curve, SEC EDGAR full-text search

**Tier 3 — If it's working and you want to scale (~$500–800/mo).** Add only after Tier 2
has produced a strategy that survived paper trading.
- **ORATS** — clean historical IV surfaces, earnings-move history, pre-computed backtests
- **Unusual Whales** (~$50–75/mo) or **Quant Data** (~$150/mo) — options flow, dark pool, dealer gamma exposure (GEX)
- **Polygon.io / Massive** Advanced (~$199/mo) — tick-level equity + options history
- **Interactive Brokers** as a second broker — for SPX/index options (cash-settled, 60/40 tax treatment, no early assignment) and better fills at size

### Do you need to build your own tool?

**Yes — one specific thing: a local options data warehouse and backtest engine.**

Nobody sells the exact thing we need, because what we need is *point-in-time correctness*
across several vendors at once. So we buy raw data and build:

1. **Ingestion + normalization** — vendor feeds → one schema → Parquet/DuckDB on disk
2. **A point-in-time feature store** — every feature timestamped with when it was *knowable*, not when it was *reported*. This is the #1 source of fake backtest profits
3. **An event-driven replay engine** — replays real historical chains with real bid/ask, and shares its code path with live trading
4. **A monitoring dashboard** — positions, greeks, P&L attribution, risk limits

**Do NOT build:** an order management system (Alpaca has one), a charting library (plotly /
TradingView), a Black-Scholes/greeks engine from scratch (`py_vollib`, QuantLib — and
ThetaData ships greeks anyway).

---

## 2. What I need from you

Answer these and I can start building. Grouped by whether they block me.

### Blocking — I can't build without these

1. **Capital.** How much is actually going into this? It changes everything — strategy
   selection, position sizing, whether index options are even reachable.
2. **Account type.** Individual taxable, or IRA? *An IRA blocks naked/undefined-risk
   positions entirely* and changes the whole strategy set. Margin or cash account?
3. **Reg-T margin or portfolio margin?** Portfolio margin needs $125k+ but massively
   improves capital efficiency for defined-risk spreads.
4. **PDT check.** If the account is under $25,000 and it's a margin account, you get 3 day
   trades per 5 business days. That kills any intraday sleeve. Under $25k we design
   around overnight holds only — tell me which side of that line you're on.
5. **Max drawdown you can actually stomach**, as a number. Not "not much." A real
   percentage. Every risk limit in the system derives from this one number.
6. **Data budget.** Which tier from Section 1.

### Blocking, but easy

7. **Alpaca account + options approval.** You need **Level 3** for spreads. Apply early —
   approval is not instant and Level 3 asks about experience.
8. **Paper trading API keys** (key + secret). Paper only for now. We will not touch live
   keys for months.

### Non-blocking — I'll assume a default if you don't answer

9. **Universe.** Default assumption: start with liquid ETFs (SPY, QQQ, IWM) plus ~40–60
   large-cap single names, expand later.
10. **Autonomy.** Default assumption: fully autonomous in paper, human-approval gate for
    the first live months.
11. **Hosting.** Default assumption: a small cloud VPS in us-east (low latency to
    exchanges) with the data warehouse local to it.
12. **Sector/ticker exclusions.** Anything you refuse to trade for personal reasons?

### Acknowledgement I want from you in writing

This is your capital. I'll build the most rigorous thing I can, and this plan has more
risk controls than most retail systems, but **systematic options trading loses money for
most people who try it.** No projected return in this document is a promise. The kill
switches in Section 5 exist because they will eventually fire.

---

## 3. Strategy design

### 3.1 Who this borrows from, and what specifically

| Source | What we actually take |
|---|---|
| **Euan Sinclair** (*Volatility Trading*) | The core thesis: edge is IV vs subsequent realized vol, not direction. Fractional-Kelly sizing. Ruthlessness about measuring edge before trading it |
| **Sheldon Natenberg** (*Option Volatility & Pricing*) | Think in surfaces, not prices. Skew and term structure are tradeable objects |
| **tastytrade / Tom Sosnoff** | The mechanical rule set: high IV rank entry, ~16-delta short strikes, 30–45 DTE, manage at 50% of max profit, close by 21 DTE. Occurrences over conviction. This is the most codifiable retail framework that exists |
| **Nassim Taleb / Mark Spitznagel** | Convexity is not optional. A small permanent long-tail allocation, financed by premium income |
| **"Karen the Supertrader"** | Cautionary tale, studied deliberately. Uncapped short strangles + no regime awareness = eventual ruin. Every naked-risk structure in this system is defined-risk instead |
| **Statistical arb tradition** (Simons et al.) | Stack many small independent edges. Trust sample size, not narrative. Purged cross-validation |

### 3.2 The regime engine (this is what makes it work both directions)

Everything routes through a regime classifier that runs pre-market and intraday. It emits
one of four states, and the state determines which sleeves are allowed to fire.

**Inputs:**

- **VIX term structure** — `VIX9D / VIX / VIX3M`. Contango = calm, backwardation = stress.
  Single best short-vol on/off switch that exists, and it's free
- **Variance risk premium** — `IV30 − realized vol(20d)`, using Yang-Zhang realized vol
  (handles gaps and overnight moves better than close-to-close)
- **Trend** — EMA 8/21/50/200 stack on SPY, ADX(14), Supertrend(10, 3)
- **Breadth** — % of S&P constituents above their 50d and 200d MA, advance/decline line
- **Credit** — HYG/LQD ratio, and IG/HY spread from FRED. Credit cracks before equities do
- **Correlation** — CBOE implied correlation index. Rising correlation = diversification stops working exactly when you need it
- **SKEW index** and 25-delta put/call skew percentile
- **Dealer gamma (GEX)** — Tier 3 only. Sign flip is a real volatility regime marker

**States and what's allowed:**

| Regime | Signature | Sleeves enabled | Posture |
|---|---|---|---|
| **RISK-ON TREND** | Contango, positive VRP, EMAs stacked up, breadth healthy | A (put spreads), B (call debit spreads), D | Net long delta, short vega |
| **CHOP / RANGE** | Contango, ADX < 20, price inside value area | A (iron condors), D | Delta neutral, short vega — best regime for us |
| **RISK-OFF TREND** | Backwardation building, EMAs stacked down, breadth deteriorating, credit widening | A (call credit spreads), B (put debit spreads), C scaled up | Net short delta |
| **CRISIS** | Sharp backwardation, correlation spike, VIX > 30 and rising | **C only.** Sleeve A entries halted, existing short premium reduced | Long convexity. Survive |

The regime engine is what lets the same system make money in both directions: in RISK-ON
we sell puts, in RISK-OFF we sell calls and buy puts, in CRISIS we stop selling and let the
hedge pay.

### 3.3 The five sleeves

**Sleeve A — Short premium (the income core, ~50–60% of risk budget)**
- Structures: put credit spreads, call credit spreads, iron condors. **Always defined risk.**
- Entry: IV Rank > 30 (prefer > 40), 30–45 DTE, short strike at ~16 delta (≈1 SD)
- Direction chosen by regime, not by prediction
- Management: close at **50% of max profit**; hard exit at **21 DTE** regardless (gamma risk goes parabolic in the last three weeks); stop at 2× credit received
- Never hold through earnings unless it's an explicit Sleeve D trade

**Sleeve B — Directional debit structures (~15–20%)**
- Structures: vertical debit spreads, diagonals, occasionally calendars
- Entry: IV Rank **< 25** (buying premium only when it's cheap), strong trend confirmation, 45–60 DTE
- This is the sleeve that pays in sustained trends, in either direction

**Sleeve C — Long convexity / tail hedge (~5–10%, permanent)**
- Structures: far OTM SPX/SPY puts 60–120 DTE, put ratio backspreads, occasionally VIX calls
- Funded continuously by Sleeve A income. Sized to be *meaningful* in a >15% drawdown
- **This sleeve loses money most months. That is its job.** It is not evaluated on
  standalone P&L; it's evaluated on what it does to portfolio-level tail risk. A rule is
  written into the system that it can never be disabled for underperformance

**Sleeve D — Event / IV crush (~10–15%)**
- Earnings: short defined-risk structures into events where implied move has *historically*
  overstated realized move for that specific ticker (needs ORATS or a self-built earnings-move
  database — minimum 12 quarters of history per name)
- Also the reverse: buy cheap vol into known catalysts when IV hasn't priced them

**Sleeve E — 0DTE intraday (0% initially, gated)**
- SPX/SPY 0DTE using opening range, GEX levels, VWAP
- **Disabled at launch.** Requires: Tier 3 data, a $25k+ account (PDT), and 6 months of
  proven live performance in Sleeves A–D. High variance and genuinely dangerous

### 3.4 Ticker screening — "meets your criteria"

Run pre-market against the universe. **Liquidity gates are hard fails, no overrides.**

**Liquidity (hard):**
- Underlying price > $20, ADV > 5M shares
- Options ADV > 5,000 contracts, open interest > 1,000 at target strikes
- Bid-ask on the target contract < 5% of mid (or < $0.10 absolute on cheap contracts)
- Weekly expirations available; penny-increment names preferred
- **Spread quality is the #1 killer of retail options bots.** A strategy with a 4% edge and
  a 6% round-trip spread cost is a losing strategy

**Signal gates (ranked, not hard fails):**
- IV Rank / IV Percentile vs sleeve requirement
- VRP: `IV30 − RV20 > threshold`, positive and in the upper half of its own 1-year range
- Ticker-level IV term structure shape
- Skew percentile vs its own history
- Liquidity-adjusted expected value after modeled slippage

**Event gates (hard):**
- Earnings date known and outside the trade window (unless Sleeve D)
- No ex-dividend inside window for short call positions (early assignment risk)
- Not on the macro blackout list (FOMC, CPI, PCE, NFP — configurable)
- No unresolved M&A / FDA / halt / going-concern flag

### 3.5 Indicators — specific parameters

You asked for specifics, so these are the exact settings the bot computes. All are used as
*filters and context*, never as standalone entry triggers.

**Trend:** EMA 8 / 21 / 50 / 200 · ADX(14), trend requires > 20 · Supertrend(10, 3) ·
linear-regression slope of 20d close

**Momentum:** RSI(14) and RSI(2) (the 2-period is for short-horizon mean reversion) ·
MACD(12,26,9) · ROC(10) · Stochastic(14,3,3)

**Mean reversion:** Bollinger %B (20, 2) · Keltner Channel (20, 2×ATR) · z-score of price
vs 20d VWAP

**Volatility:** ATR(14) and ATR percentile · Yang-Zhang realized vol (10d, 20d, 60d) ·
Bollinger Band Width percentile · **TTM Squeeze** (BB inside Keltner = compression,
expansion = release; genuinely useful for timing long-vol entries)

**Volume:** RVOL vs 20d average · OBV · session VWAP with ±1/±2 SD bands · **anchored VWAP**
from significant pivots (earnings, swing highs/lows, gaps)

**Support / resistance — computed, not eyeballed:**
- Swing pivots via fractal detection (5-bar), clustered to find repeated-touch levels
- **Volume profile**: POC, Value Area High/Low — where price actually traded, which is far
  more meaningful than trendlines
- Prior day high/low/close, prior week high/low
- Overnight range high/low, opening range (first 15 and 30 min)
- Round numbers and high-open-interest strikes (these genuinely act as magnets — pin risk is real)

**Bar patterns** — engulfing, inside bar, outside bar, NR7, pin bar/hammer, 3-bar reversal.
**Strong caveat:** these are the weakest inputs in the system. Most published candlestick
edges do not survive honest out-of-sample testing. Each pattern must independently clear
the Section 6 statistical bar before it's allowed to influence a single trade. I expect
most of them to fail that test, and they get dropped when they do.

**Multi-timeframe:** 5m / 15m / 1h / daily. Higher timeframe sets bias; lower timeframe
times entry.

### 3.6 News and macro

- **Earnings calendar** — non-negotiable. Accidentally holding short premium through an
  earnings print is a classic, avoidable, account-damaging error
- **Economic calendar** — CPI, PCE, FOMC, NFP, claims, GDP. These get a *volatility event*
  treatment: no new short-vega entries in the 24h before, sizing reduced
- **Headline sentiment** — Alpaca's Benzinga feed to start. An LLM scores headlines for
  materiality and direction; the score is a *veto* input (can block a trade) rather than a
  *trigger* input (can't create one). Sentiment data is noisy and gameable; giving it
  trigger authority is asking to be farmed
- **Analyst revisions, guidance changes, 8-K filings** via EDGAR full-text search

---

## 4. Architecture

```
options-bot/
├── data/
│   ├── ingest/        # vendor adapters: alpaca, thetadata, cboe, fred, news
│   ├── normalize/     # one schema for chains, bars, greeks, events
│   └── store/         # DuckDB + Parquet; point-in-time correct
├── features/
│   ├── technical/     # the Section 3.5 indicator library
│   ├── vol/           # IV rank/percentile, VRP, term structure, skew, surfaces
│   ├── regime/        # the Section 3.2 classifier
│   └── store.py       # feature store — every feature carries a knowable_at timestamp
├── strategy/
│   ├── screener.py    # Section 3.4 universe filtering
│   ├── sleeves/       # a.py b.py c.py d.py e.py
│   └── sizing.py      # fractional Kelly, capped
├── risk/
│   ├── limits.py      # portfolio greeks, concentration, buying power
│   ├── circuit.py     # kill switches
│   └── beta_weight.py # everything beta-weighted to SPX
├── execution/
│   ├── broker/        # Alpaca adapter (interface allows a second broker later)
│   ├── router.py      # multi-leg construction, limit pricing, retry ladder
│   └── fills.py       # slippage tracking — backtest assumptions vs live reality
├── engine/
│   ├── events.py      # shared event loop
│   ├── backtest.py    # historical replay
│   └── live.py        # same code path, different data source
├── monitoring/
│   ├── dashboard/     # positions, greeks, P&L attribution, limit utilization
│   └── alerts/        # push on limit breach, kill switch, reconciliation failure
└── config/            # YAML, version-controlled, hash-logged with every trade
```

**Non-negotiable architectural rules:**

1. **Backtest and live share one code path.** The engine differs only in where events come
   from. Any strategy code that can tell whether it's in a backtest is a bug — that's how
   backtest/live divergence happens.
2. **Point-in-time everything.** Every feature records when it was *knowable*. Earnings
   dates get revised, analyst estimates get restated, index membership changes. Using
   today's values in a 2019 backtest is lookahead bias and it manufactures fake profit.
3. **Config-driven and hash-logged.** Every trade logs the config hash and git SHA that
   produced it. When something goes wrong in six months we need to reconstruct exactly what
   the bot believed.
4. **Reconciliation loop.** Every N minutes, compare the bot's internal position state
   against the broker's actual positions. Mismatch = halt and alert. Assume divergence will
   happen, because it will.

**Stack:** Python 3.12 · Polars/Pandas · DuckDB + Parquet · `py_vollib`/QuantLib for greeks ·
`alpaca-py` · FastAPI + a small React dashboard · Docker · pytest.

---

## 5. Risk management

This section matters more than the strategy section. Strategies decay; risk controls are
what keep you solvent long enough to build the next one.

**Per trade**
- Max loss ≤ 1–2% of account equity (defined-risk structures make this exact)
- Position sizing: **quarter-Kelly**, capped. Full Kelly is theoretically optimal and
  practically ruinous because our edge estimate is itself uncertain
- Every position defined-risk. No naked short options. No exceptions

**Portfolio**
- **Net beta-weighted delta** held within a band (e.g. ±0.30 delta per $1k of equity).
  Beta-weighted to SPX — eight put credit spreads on eight tech names is one position, not
  eight
- **Net vega cap** — the real constraint on a premium seller. It's what turns a bad week
  into a blown account
- **Net gamma floor** — a hard limit on how short gamma we're allowed to be
- Buying power utilization < 40%. Sounds conservative. Isn't
- Max positions per underlying, per sector, per expiration cycle
- Correlation-aware: if implied correlation spikes, effective position count collapses and
  gross exposure gets cut automatically

**Circuit breakers (automatic, no discretion)**
- Daily loss limit → halt new entries for the session
- Weekly loss limit → halt, require manual review
- Max drawdown from high-water mark → **full kill switch**, flatten to hedge-only, human required to restart
- N consecutive losing trades → pause that sleeve, review
- Data staleness / quote gap / broker disconnect → halt entries immediately
- Reconciliation mismatch → halt everything

**Assignment and expiration**
- Auto-close short legs at 21 DTE (already in Sleeve A rules)
- Early-assignment monitor on short ITM calls approaching ex-dividend
- Never let a spread go to expiration with one leg ITM — pin risk turns defined risk into
  an overnight naked position

---

## 6. Backtesting and validation

This is where the project succeeds or fails. Most retail bots die here and their owners
don't find out until real money is on the line.

**Data requirements:** real historical chains with actual bid/ask. Not Black-Scholes
reconstruction from underlying prices — that fabricates fills that never existed and it
will make almost any strategy look profitable.

**Cost model (deliberately pessimistic):**
- Fill at **40% toward the unfavorable side** of the spread, not at mid
- Commissions modeled even though Alpaca is $0 (so results survive a broker change)
- Slippage scaled by contract liquidity and time of day
- Assignment and exercise costs
- **Rule: if the strategy only works at mid-price fills, it doesn't work**

**Validation protocol:**
- **Walk-forward** analysis, never a single in-sample fit
- **Purged, embargoed cross-validation** (López de Prado) — overlapping options positions
  leak information across naive train/test splits
- **Deflated Sharpe ratio** — corrects for the number of variants tested. Test 200 things
  and something looks great by luck; DSR prices that in
- **Regime-stratified reporting.** Required windows: Q4 2018, Mar 2020, all of 2022,
  Aug 2024, Apr 2025. A strategy that is only profitable in aggregate but loses badly in
  every stress window is not deployable
- Minimum ~200 trades per sleeve before any conclusion
- Monte Carlo trade-order reshuffling to get a realistic drawdown distribution

**Metrics that actually matter:** CAGR, max drawdown, **Sharpe and Sortino**, Calmar,
win rate paired with win/loss ratio (win rate alone is meaningless for premium selling —
85% win rates are trivial and frequently disastrous), tail ratio, worst single day, worst
month, P&L attribution by sleeve and by regime.

**Paper trading gate:** minimum **3 months** live paper before any real capital. The
comparison that matters is *paper fills vs backtest-assumed fills*. If live slippage
exceeds the model, the backtest was fiction and we go back to Section 6.

---

## 7. Build phases

| Phase | Duration | Deliverable | Gate to advance |
|---|---|---|---|
| **0. Setup** | 1 wk | Accounts, API keys, data subscriptions, repo scaffolding, CI | Keys work, data flowing |
| **1. Data layer** | 2–3 wks | Ingestion, normalization, DuckDB warehouse, point-in-time feature store | Can reconstruct any historical chain on demand; PIT correctness test suite green |
| **2. Feature library** | 2 wks | All Section 3.5 indicators, vol metrics, regime classifier | Indicators match TradingView on spot checks; regime labels match visual inspection of history |
| **3. Backtest engine** | 3 wks | Event-driven replay, realistic cost model | Reproduces a known-result benchmark strategy |
| **4. Sleeve A** | 2–3 wks | Short premium, fully backtested | Passes Section 6 in full, including all stress windows |
| **5. Risk layer** | 2 wks | Limits, circuit breakers, beta weighting, reconciliation | Every breaker fires correctly in simulation |
| **6. Sleeves B + C** | 3 wks | Directional + tail hedge | Portfolio-level results beat Sleeve A alone on Calmar |
| **7. Execution + paper** | 2 wks | Alpaca integration, multi-leg routing, dashboard | Paper trades executing, fills tracked vs model |
| **8. Paper trading** | **3 months** | Live paper operation | Live slippage within model; no unexplained divergence |
| **9. Sleeve D** | 2 wks | Earnings / IV crush | Passes validation |
| **10. Live, small** | 3+ months | Real money at ~10–20% of intended size | Live results consistent with paper |
| **11. Scale / Sleeve E** | — | Size up, consider 0DTE | Sustained live performance |

**Realistic timeline to first live dollar: ~6–8 months.** Most of that is Phases 1–3 and
the 3-month paper gate. Anyone promising faster is skipping the parts that prevent losses.

---

## 8. Immediate next steps

**You:**
1. Answer the Section 2 blocking questions
2. Open the Alpaca account and apply for **Level 3** options approval (do this today — it's the long pole)
3. Decide on the data tier and purchase; ThetaData is the key one
4. Send me paper trading API keys

**Me, once I have the above:**
1. Scaffold the repo per Section 4 with CI and test harness
2. Build the data layer and prove point-in-time correctness
3. Stand up the regime classifier and validate its labels against known historical regimes
4. Report back with the first real backtest of Sleeve A across all stress windows —
   including if the answer is "this doesn't work"

---

## Sources

- [Multi-Leg (Level 3) Options Trading Now Available at Alpaca](https://alpaca.markets/blog/level-3-options-trading-now-available-with-alpacas-trading-api/)
- [Alpaca Docs — Options Level 3 Trading](https://docs.alpaca.markets/docs/options-level-3-trading)
- [Alpaca Docs — Historical Option Data](https://docs.alpaca.markets/us/docs/historical-option-data)
- [Alpaca Docs — About Market Data API](https://docs.alpaca.markets/us/docs/about-market-data-api)
- [Alpaca — Options Backtesting Guide](https://alpaca.markets/learn/backtesting-your-options-trading-strategies)
- [Alpaca — Market Data Plans](https://alpaca.markets/data)
- [ThetaData — Options Data](https://www.thetadata.net/options-data)
- [ORATS — Historical Options Data (since 2007)](https://orats.com/near-eod-data)
- [Polygon.io / Massive — Pricing](https://polygon.io/pricing)
- [Best Options Data APIs 2026 — comparison](https://flashalpha.com/articles/best-options-data-apis-2026)
- [Unusual Whales](https://unusualwhales.com/)
- [Quant Data — Gamma Exposure API](https://help.quantdata.us/en/articles/15807345-gamma-exposure-gex-api-python-quickstart-dealer-positioning-guide)
