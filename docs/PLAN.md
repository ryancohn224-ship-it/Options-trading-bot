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

### What to buy, and when — phased for a small account

An earlier draft of this plan recommended ~$250/mo of ongoing data. **On a sub-$25k
account that is a bad recommendation** — $3,000/yr against $20k of capital is a 15%
annual drag, meaning the strategy has to clear 15% before you've made a dollar. That
alone could be the difference between edge and no edge.

So the spend is phased instead. Historical data is a **bounded R&D purchase**, not a
subscription you carry forever.

**Phase 1 — Research and backtesting (months 1–5): ~$400–600 total**
- **ThetaData**, ~$100/mo for the ~4 months we're actively building and validating. Download the history, store it locally, then **cancel**. Once the data is in our warehouse it's ours; we don't need to keep renting it
- Everything else free: Alpaca paper account, CBOE index data (VIX, VIX9D, VIX3M, SKEW, put/call ratios), FRED macro, SEC EDGAR
- TradingView free tier is fine for eyeballing. Skip the paid plan for now

**Phase 2 — Paper trading (months 6–8): $0–99/mo**
- Our core sleeves make decisions **once a day**, not intraday. Alpaca's free tier serves data older than 15 minutes, which is fine for generating end-of-day signals
- Add **Algo Trader Plus ($99/mo)** only when we start measuring fill quality seriously — we need live NBBO to know whether our slippage model is honest
- Realistically: free for the first month or two, then $99/mo

**Phase 3 — Live: $99/mo**
- Algo Trader Plus for real-time OPRA. On $20k that's ~6%/yr — real, but tolerable, and it scales down as a percentage as the account grows

**Total cost to reach first live dollar: roughly $700–1,000.** That's the honest number.

**Later, only if it's working and the account has grown:** ORATS (historical IV surfaces,
earnings-move history), Unusual Whales or Quant Data (flow, dealer gamma), Polygon/Massive
(tick data), Interactive Brokers as a second broker. None of these are needed to find out
whether the strategy has an edge, and none should be bought before it does.

### Do you need to build your own tool?

**Yes — one specific thing: a local options data warehouse and backtest engine.**

Nobody sells the exact thing we need, because what we need is *point-in-time correctness*
across several vendors at once. So we buy raw data and build:

1. **Ingestion + normalization** — vendor feeds → one schema → Parquet/DuckDB on disk
2. **A point-in-time feature store** — every feature timestamped with when it was *knowable*, not when it was *reported*. This is the #1 source of fake backtest profits
3. **An event-driven replay engine** — replays real historical chains with real bid/ask, and shares its code path with live trading
4. **A monitoring dashboard** — positions, greeks, P&L attribution, risk limits

**Do NOT build:** an order management system (Alpaca has one), a charting library (plotly /
TradingView), a Black-Scholes/greeks engine from scratch (`py_vollib` — and ThetaData ships
greeks anyway).

**→ [Section 4.3](#43-what-the-data-warehouse-actually-is) explains concretely what the
warehouse is** — it's a folder of files, not infrastructure.

---

## 2. Your account profile and what it constrains

**Confirmed:** regular taxable account (not an IRA), under $25,000, paper trading first
until an edge is demonstrated.

### The PDT rule probably doesn't bind anymore — but verify

This changed recently and it's good news. On **April 14, 2026** the SEC approved
amendments to FINRA Rule 4210 that **eliminated the pattern-day-trader designation and
the $25,000 minimum equity requirement**, effective **June 4, 2026**. It's replaced by an
intraday margin monitoring standard applied at the firm level. Brokers have until
**October 20, 2027** to implement, so whether it still binds *you* depends entirely on
where Alpaca is in that transition.

**Action: ask Alpaca support directly whether day-trade counting is still enforced on
sub-$25k margin accounts.** Don't assume either way.

**It mostly doesn't matter for us regardless**, and that's by design. Sleeves A–D all hold
positions for days to weeks — 30–45 DTE entries, managed at 50% profit, closed by 21 DTE.
Almost nothing opens and closes the same session. So:

- The core strategy is **unaffected** either way
- Only **Sleeve E (0DTE intraday)** ever depended on this, and it was already disabled
- **One real engineering requirement:** a position that hits its 50%-profit target on the
  same day it was opened would be a same-day round trip. The bot needs a day-trade counter
  and a "no same-day close" guard, active whenever Alpaca still enforces PDT. Small piece
  of code, easy to forget, causes a compliance flag if you do

### What a sub-$25k account actually constrains

The binding constraint isn't buying power — defined-risk spreads are cheap in BP terms.
It's **granularity**. At $20k with a 1–2% per-trade risk limit, you have **$200–400 of max
loss per position**, and options come in discrete sizes.

- A SPY **$5-wide** put credit spread risks ~$500 max. **Too big** — that's one trade
  putting 2.5% at risk
- A SPY **$1–2-wide** spread risks ~$100–180. **This is the workable size**, roughly 0.5–1%
- Expect **5–8 concurrent positions**, not 20. Diversification is genuinely limited, which
  makes the beta-weighting and correlation controls in Section 5 more important, not less
- **Alpaca's $0 options commission matters enormously here.** At $0.65/contract, four legs
  round-trip is $5.20 against maybe $60 of credit — a 9% haircut that would sink this.
  This is the main reason Alpaca is the right broker for a small account
- **Sleeve C (tail hedge) is proportionally expensive.** Far-OTM SPY puts cost real money
  against $20k. It stays in — it's the reason the system survives a crash — but sized to
  perhaps 3–5% rather than 5–10%, and we lean on put ratio backspreads, which can be
  structured for little or no net debit

### XSP is the thing to watch

Alpaca added **index options in paper trading on July 23, 2026** — SPX, SPXW, VIX, VIXW,
DJX, and **XSP**. XSP is one-tenth the size of SPX, and for a small account it's close to
ideal:

- **Cash-settled and European-style** — no early assignment, no pin risk turning a defined
  spread into an overnight naked position
- **Section 1256 tax treatment** (60% long-term / 40% short-term regardless of holding
  period) — a genuine, structural after-tax advantage over SPY options in a taxable account
- Sized so a small account can build real positions

**Caveat: index options are paper-only on Alpaca right now**, with live "coming soon." So:
build and validate on XSP in paper, and plan for SPY/ETF options as the live fallback if
index options haven't gone live by the time we're ready. The strategy code shouldn't care
which — that's an argument for keeping the instrument choice in config.

### Still open — I need these to proceed

1. **Capital, as a number.** "Under $25k" spans $5k and $24k, and those are different
   systems. Below roughly $10k I'd argue the fixed data cost makes this not worth doing yet
2. **Margin or cash account?** Spreads require margin approval — a cash account caps you at
   Level 1–2 (long options, covered calls, cash-secured puts) and most of this plan
   becomes unavailable
3. **Max drawdown you can actually stomach, as a percentage.** Not "not much." Every risk
   limit in Section 5 derives from this single number
4. **Alpaca account + Level 3 options approval** — apply now, it's the long pole
5. **Paper trading API keys** once you have them

Defaults I'll assume unless you say otherwise: universe starts with liquid ETFs (SPY, QQQ,
IWM) plus ~40–60 large caps; fully autonomous in paper with a human approval gate for early
live trading; hosted on a small VPS.

### Worth saying plainly

This is your capital. This plan has more risk controls than most retail systems, but
**systematic options trading loses money for most people who attempt it**, and a small
account has thinner margins for error than a large one. No number in this document is a
projection or a promise. The kill switches in Section 5 exist because they will eventually
fire. Your instinct to prove the edge in paper first is the correct one, and it's the gate
I'd hold you to even if you hadn't asked for it.

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
- SPXW/SPY 0DTE using opening range, GEX levels, VWAP
- **Disabled at launch.** Requires: flow/GEX data, confirmation that day-trade counting no
  longer applies (see Section 2), and 6 months of proven live performance in Sleeves A–D.
  High variance and genuinely dangerous — the PDT repeal makes this *possible*, not *advisable*

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

## 4. What we're actually building

You asked two fair questions: what's the stack, and what is this "data warehouse." The
previous draft listed options instead of making calls. Here are the calls.

### 4.1 The whole thing is three programs sharing one library

That's the clearest way to think about it. Not a platform — three programs:

| # | Program | Runs | Job |
|---|---|---|---|
| 1 | **Loader** | Nightly, ~10 min | Pull yesterday's data from vendors, clean it, append it to local storage |
| 2 | **Backtester** | On demand | Replay stored history through the strategy, simulate fills, produce a performance report |
| 3 | **Trader** | Every morning, ~1 min | Read today's data, run **the same strategy code**, place real orders at Alpaca |

The critical part is that **2 and 3 import the identical strategy module**. The backtester
feeds it historical data; the trader feeds it live data. Neither knows the difference. This
is the single most important design decision in the project, because the standard way these
systems fail is that the backtest and the live bot quietly diverge and nobody notices until
real money is gone.

### 4.2 The stack — decided

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | Every options/quant library lives here |
| Dataframes | **Polars** | Much faster than pandas on the row counts we'll hit; pandas only where a library demands it |
| Storage | **DuckDB + Parquet files** | This *is* the "data warehouse." See below |
| Broker + live data | **`alpaca-py`** | Official SDK |
| Greeks / IV | **`py_vollib`** | Fast Black-Scholes-Merton. ThetaData ships greeks anyway; this fills gaps and cross-checks |
| Config | **YAML validated by Pydantic** | Strategy params never hardcoded; every trade logs its config hash |
| Dashboard | **Streamlit** | ~200 lines gets positions, greeks, P&L, and risk-limit gauges. One user, one screen |
| Tests | **pytest** | |
| Scheduling | **cron** | It's three jobs a day |
| Hosting | **Docker on a $10–20/mo VPS** (Hetzner or DigitalOcean, us-east) | |
| CI | **GitHub Actions** | Tests on every push |

**Explicitly rejected, so there's no ambiguity:**

- ~~FastAPI + React dashboard~~ — I proposed this in the first draft and it was overkill. Streamlit, for one user
- ~~Postgres / TimescaleDB~~ — a database server you'd have to run and back up, for data that one person queries. DuckDB reads Parquet directly
- ~~Kafka, Airflow, dbt~~ — orchestration for a pipeline that is three cron jobs
- ~~Snowflake / BigQuery~~ — cloud warehouses for datasets that fit on a laptop
- ~~Backtrader, Zipline, VectorBT~~ — **the important one.** These are equities-first. None of them model multi-leg options positions against real historical chains with bid/ask, per-leg greeks, assignment, and expiration. Trying to bend them into doing it is more work than writing the engine, and you inherit assumptions you can't see. This is the honest reason we build our own

Total infrastructure cost: **~$10–20/mo**.

### 4.3 What the "data warehouse" actually is

The term oversells it. **It is a folder of files on disk, plus a library that runs SQL
against them.** No server, no cluster, nothing to administer.

```
warehouse/
├── option_chains/      # the big one: every contract, every day
│   └── date=2024-03-15/underlying=SPY/data.parquet
├── equity_bars/        # daily + minute OHLCV for underlyings
├── vol_indices/        # VIX, VIX9D, VIX3M, SKEW, put/call ratios
├── macro/              # FRED series, credit spreads, yield curve
├── events/             # earnings dates, dividends, splits, econ calendar
└── features/           # computed indicators, cached
```

`option_chains` holds, for every trading day and every contract: strike, expiration, bid,
ask, last, volume, open interest, IV, and greeks. That's the raw material every backtest
runs on. For ~60 tickers across ~4 years, expect roughly **10–40 GB** after Parquet
compression. It fits on a laptop SSD.

DuckDB queries it with plain SQL — `SELECT * FROM 'warehouse/option_chains/**/*.parquet'
WHERE ...` — with no import step and no server process.

**Why not just call the vendor API each time we need data?** Four reasons, and the fourth
is the real one:

1. **Speed.** A single backtest touches millions of rows. Over the API that's hours; from
   local Parquet it's seconds. You will run the backtest *hundreds* of times while
   developing — this difference decides whether the project is workable
2. **Cost.** Vendors rate-limit and meter. Download once, query forever. It's also what
   lets us cancel ThetaData after the research phase and keep the data
3. **Reproducibility.** The same backtest returns the same answer in six months. Vendor
   APIs silently revise history
4. **Point-in-time correctness — the one that actually matters.** Vendors hand you *today's*
   view of the past. Earnings dates get rescheduled, dividends get revised, index membership
   changes, tickers get renamed. If a 2022 backtest looks up an earnings date using a 2026
   calendar, the bot "knew" a date that hadn't been announced yet. That's lookahead bias, it
   is completely invisible in the results, and it manufactures profit that does not exist.
   The fix is storing a `knowable_at` timestamp on every row and filtering every query by it.
   **No vendor does this for you.** It is the specific reason we build this piece ourselves

The live trader writes into the same warehouse each day, so tomorrow's backtest
automatically includes today. Research and production share one source of truth.

### 4.4 Repo layout

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
│   ├── dashboard.py   # Streamlit: positions, greeks, P&L attribution, limit utilization
│   └── alerts/        # push on limit breach, kill switch, reconciliation failure
└── config/            # YAML, version-controlled, hash-logged with every trade
```

Mapping back to Section 4.1: the **Loader** is `data/`, the **Backtester** is
`engine/backtest.py`, the **Trader** is `engine/live.py`, and `strategy/` + `features/` +
`risk/` are the shared library both of them call.

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
5. **Day-trade guard.** Per Section 2 — a counter plus a no-same-day-close rule, active
   while Alpaca still enforces day-trade counting.

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
1. Open the Alpaca account and apply for **Level 3** options approval — do this first, it's
   the long pole, and confirm it's a **margin** account
2. Ask Alpaca support: *is day-trade counting still enforced on sub-$25k margin accounts,
   or have you implemented the June 2026 Rule 4210 changes?*
3. Tell me the capital number and your max acceptable drawdown percentage
4. Send me paper trading API keys
5. Hold off on ThetaData until I've built the loader — no reason to start the meter running
   before there's something to load into

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
- [Index Options Now in Paper on Alpaca's Trading API](https://alpaca.markets/blog/alpaca-introduces-index-options-paper-trading/)
- [WilmerHale — SEC Approves Amendments to FINRA Rule 4210 Replacing Day Trading Margin Requirements](https://www.wilmerhale.com/en/insights/client-alerts/20260423-sec-approves-amendments-to-finra-rule-4210-replacing-day-trading-margin-requirements-with-a-modernized-intraday-margin-standard)
- [Schwab — SEC Approves Scrapping $25,000 Day Trader Minimum](https://www.schwab.com/learn/story/sec-approves-scrapping-25000-day-trader-minimum)
- [Cboe — XSP (Mini-SPX) Options](https://www.cboe.com/tradable-products/sp-500/xsp-options)
