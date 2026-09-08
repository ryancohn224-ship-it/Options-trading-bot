# Ground-Up Redesign — Research Report

**Status:** Research only. No code, and `PLAN.md` is untouched. This is what I'd build
if the target were consistent, strong returns rather than "a reasonable retail options bot."
**Date:** 2026-09-08

---

## 0. First, what "consistent strong daily returns" actually means

Before designing for it, it's worth being exact about the target, because the phrase
describes something no one has achieved — including Renaissance.

Under a normal approximation, the probability of a positive period is Φ(Sharpe / √periods).
Here is what that gives for annualized Sharpe ratios from "plain premium selling" up to
"best fund in history":

| Annual Sharpe | Who lives here | P(positive day) | P(positive month) | P(positive quarter) |
|---|---|---|---|---|
| 0.47 | S&P 500, 1986–2015 | 51.2% | 55.4% | 59.3% |
| 0.67 | **Cboe PUT index** (systematic put-writing, 30 yrs) | 51.7% | 57.7% | 63.1% |
| 1.0 | A good systematic vol program | 52.5% | 61.4% | 69.1% |
| 1.5 | An excellent one; realistic ceiling for retail | 53.8% | 66.7% | 77.3% |
| 2.0 | Top-tier multi-strat | 55.0% | 71.8% | 84.1% |
| 3.0 | Medallion-class, net | 57.5% | 80.7% | 93.3% |

The takeaway that shapes everything below: **daily consistency does not exist at any
achievable Sharpe.** At Sharpe 1.5, 46% of days lose money. Even at Medallion-class 3.0,
42% of days lose. Consistency is a *monthly and quarterly* phenomenon, and it comes from
Sharpe, which comes from three things — and none of them is "a better indicator."

The honest benchmark for this project is the Cboe PUT index: Sharpe 0.67, ~9.5% CAGR at
~10% vol, over 30 years ([Cboe/Bondarenko 2019](https://cdn.cboe.com/resources/education/research_publications/PutWriteCBOE19_v14_by_Prof_Oleg_Bondarenko_as_of_June_14.pdf)).
That's what mechanical premium selling earns. Everything in this redesign is about what
lifts a system from 0.67 toward 1.5. If a backtest shows 2.5+ net of realistic costs, the
correct response is to look for the bug.

---

## 1. What I'd throw out of the current plan, and the evidence for each

I wrote the current plan. Rereading it against the literature, here's what doesn't survive.

### 1.1 The technical-indicator library (§3.5)

RSI, MACD, Stochastics, Bollinger %B, Supertrend, ADX, bar patterns. I included these
because they were asked for and hedged them as "filters." The honest position is stronger:
**they should mostly go.**

[Sullivan, Timmermann & White (1999, *Journal of Finance*)](https://onlinelibrary.wiley.com/doi/10.1111/0022-1082.00163)
re-tested the classic Brock–Lakonishok–LeBaron technical-rule results across a universe of
7,846 rules using a bootstrap "reality check" for data snooping. After correction, the
out-of-sample evidence for technical rules weakened substantially; over the subsequent
decade the best in-sample rules had no significant predictive content. Every indicator in
§3.5 is a member of that family. Their apparent edge in backtests is overwhelmingly the
result of trying many of them.

**What survives from that section**, because each has independent academic support:
realized-volatility estimators (Yang-Zhang, and HAR — see §3), 12-1 month momentum
(Jegadeesh–Titman), 1-week short-term reversal, and VWAP — used for *execution timing*,
not signal generation.

### 1.2 The put-buying tail hedge (Sleeve C)

The plan carries a permanent 3–10% allocation to far-OTM puts and forbids disabling it.
That's the textbook retail answer, and AQR's research says it's wrong.

[AQR, "Tail Risk Hedging: Contrasting Put and Trend Strategies" (2020)](https://www.aqr.com/Insights/Research/White-Papers/Tail-Risk-Hedging-Contrasting-Put-and-Trend-Strategies):
long-OTM-put strategies have **consistently negative long-term returns**, while
multi-asset trend-following delivered positive long-term returns *and* mitigated losses in
equity tail events. Their conclusion is a preference for Trend over Put.
[Israelov & Nielsen, "Still Not Cheap" (JPM 2015)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2579232)
explains why: from 1990–2014 the volatility risk premium averaged 3.4% and was positive
88% of the time — so a standing put buyer is paying that premium continuously. Buying puts
"when IV is low" doesn't help; what matters is IV relative to realized, not IV relative to
its own history.

**Replacement:** three things that provide crisis protection with positive or zero expected
cost — (a) defined-risk structures everywhere, so convexity is built into every position
rather than bought separately, (b) volatility-targeting (§2.2), which cuts exposure
mechanically as realized vol rises, and (c) a trend-following overlay that scales net delta.
A small, *conditional* put allocation is still reasonable when the term structure and skew
say protection is unusually cheap relative to forecast — but as a tactical position, not a
permanent sleeve.

### 1.3 IV Rank as the entry filter

Sleeve A enters on "IV Rank > 30." Israelov–Nielsen make the case directly: the level of
implied vol relative to its own history tells you nothing about whether an option is rich.
**IV relative to a forecast of realized vol** does. §3.1 replaces IV Rank with a proper
VRP estimate.

### 1.4 Hand-coded regime thresholds

The regime engine in §3.2 of the plan is a set of if-statements on VIX term structure,
ADX, and breadth. It'll work roughly, but every threshold is a free parameter fit by eye.
Replacement: a 2–3 state Hidden Markov Model on daily SPY returns and VIX changes
(Hamilton 1989 lineage), which gives regime *probabilities* rather than labels and has no
hand-tuned thresholds. Paired with the HAR vol forecast, most of what the regime engine was
doing gets done better by the forecaster.

### 1.5 Fixed structural rules — "16-delta, 45 DTE, close at 50%, exit at 21 DTE"

These are the tastytrade defaults. They're good priors and they should remain the defaults.
But they were chosen as *rules of thumb for humans*, and a system shouldn't be bound by
them: strike, expiry, and exit should be chosen by expected edge net of cost, with these as
the fallback when the optimizer has nothing better. Section 3.5 covers this.

### 1.6 "Sleeves" organized by structure

The plan organizes strategy by *what you trade* (credit spreads, debit spreads, calendars).
A stronger design organizes by *where the money comes from* — the source of edge — and lets
the structure follow. §3 is organized that way.

### 1.7 0DTE (Sleeve E)

Already disabled in the plan, but now with hard evidence. [Vilkov's 0DTE study
(SPXW, Sep 2016–Jan 2026)](https://github.com/vilkovgr/0dte-strategies/blob/main/docs/paper/paper-annotated.md)
tested seven strategy families with strict no-look-ahead discipline: the same-day VRP is
"positive but economically negligible after realistic frictions" — median ~0.001% of spot.
An equal-weight basket of all strategies nets **Sharpe 0.25**. Only conditioned models
(logistic, on regime features) reached net Sharpe 0.8–0.9, with 1% expected shortfall of
0.6–1.6% of the underlying per day. And PnL was driven by *realized skewness* — direction —
not variance. That's a directional bet dressed as a vol trade. It stays out.

---

## 2. The design principle

Three results from the literature explain nearly all of the gap between Sharpe 0.67 and
Sharpe 1.5. The redesign is built around them.

### 2.1 Breadth — Grinold & Kahn's fundamental law

`IR = IC × √Breadth` ([Grinold 1989](https://blankcapitalresearch.com/learn/grinold-fundamental-law-active-management)).
Information ratio equals skill times the square root of the number of *independent* bets
per year. A modest edge applied across many uncorrelated decisions beats a strong edge
applied to a few. **This is the mechanism by which consistency is manufactured.** The
current plan runs 5–8 positions from a handful of correlated sleeves; §3.3 aims for many
positions across *independent sources of edge*, so a bad month for one source is offset by
the others.

### 2.2 Volatility targeting — Moreira & Muir

[Moreira & Muir (2017, *Journal of Finance*)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2659431):
scaling exposure by the inverse of recent realized variance **raised Sharpe ratios by
50–100%** across the market, value, momentum, and carry factors, with the mechanism being
that variance is highly forecastable at short horizons while expected returns are not — so
cutting size when variance spikes loses little return and removes a lot of risk. The
strategy "cut exposure most aggressively during the GFC and COVID." For a premium-selling
system, whose worst losses arrive exactly when realized vol spikes, this is the single
highest-value change in this document. It's also nearly free to implement.

### 2.3 Relative value — cancelling the beta

Every position in the current plan is an *outright* vol position: short vol or long vol.
Outright short vol carries market beta — it loses in every crash. Relative-value structures
(short one vol, long a related vol) cancel most of that beta and leave the premium.
Dispersion (§3.2, F4) is the canonical example. This is how institutional vol funds
achieve monthly consistency that outright sellers can't.

---

## 3. The redesigned system

Seven layers. Layers 1, 6 and 7 are close to the current plan; 2–5 are new.

### 3.1 Layer 2 — Forecasting (new)

The current plan compares IV to IV's own history. The redesign compares IV to a *forecast*
of realized vol. That forecaster is the heart of the system.

- **HAR-IV realized-volatility model.** The heterogeneous autoregressive model
  ([Corsi 2009](https://www.researchgate.net/publication/382306092_Heterogeneous_Autoregressive_HAR_Models_of_Realized_Volatility_Specifications_Properties_and_Estimation))
  regresses next-period RV on daily, weekly, and monthly lagged RV. It's the workhorse of
  the academic vol-forecasting literature, beats GARCH, and is a linear regression.
  [Kambouroudis et al. (2021)](https://onlinelibrary.wiley.com/doi/full/10.1002/fut.22241)
  show that adding implied vol as a regressor (HAR-IV) beats every HAR variant without it,
  and that overnight returns and the leverage effect add further accuracy in US data.
  **Edge for any contract = IV − HAR-IV forecast**, per ticker, per horizon.
- **Earnings-move forecaster.** Per-ticker model of realized earnings move vs implied, using
  at least 12 quarters of history plus the LLM event features in F8.
- **HMM regime model.** 2–3 state Gaussian HMM on SPY returns and ΔVIX. Outputs
  regime probabilities that feed sizing and factor weights.

### 3.2 Layer 3 — Alpha factors (replaces sleeves)

Each factor is a function that maps (ticker, structure, expiry) → expected edge and a
confidence. Each has its own continuously-measured Information Coefficient. The factors,
with the evidence that they exist:

| # | Factor | Source of edge | Evidence | Structure it maps to |
|---|---|---|---|---|
| **F1** | **Variance risk premium** | IV persistently above subsequent RV | VRP averaged 3.4%, positive 88% of months 1990–2014 ([Israelov–Nielsen](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2579232)); majority of index VRP is correlation premium ([DMV 2009](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID1157804_code417015.pdf?abstractid=673425)) | Delta-neutral short premium: iron condors, strangles-as-condors, where IV − HAR forecast is largest |
| **F2** | **Skew premium** | OTM puts overpriced vs realized crash frequency, driven by institutional hedging demand and limits to arbitrage | [Ruf, "Limits to Arbitrage and the Skewness Risk Premium"](https://www.bauer.uh.edu/departments/finance/documents/seminars/Ruf_013112.pdf) | Put credit spreads and risk reversals when 25-delta skew is rich vs its forecast |
| **F3** | **Term-structure roll-down** | Vol curve in contango ~80% of the time; front-month decays faster than back | [Quantpedia, VIX term structure](https://quantpedia.com/strategies/exploiting-term-structure-of-vix-futures) | Calendar and diagonal spreads on the underlying's own surface; VIX-based sizing signal |
| **F4** | **Correlation premium (dispersion)** | Implied index correlation ~47% vs realized ~29% — an 18-point premium | [Driessen, Maenhout & Vilkov 2009](https://www.researchgate.net/publication/227347954_The_Price_of_Correlation_Risk_Evidence_from_Equity_Options) | Short index vol vs long constituent vol. The one genuinely *market-neutral* premium |
| **F5** | **Earnings-move premium** | Stocks close inside the implied move 70–75% of the time, with strong cross-sectional persistence in *which* names overprice | [Gao, Xing & Zhang (JFQA 2018)](https://www.ruf.rice.edu/~yxing/straddle_201305_03.pdf); [ORATS](https://orats.com/blog/earnings-straddles-strong-season-2026) | Defined-risk short structures into earnings on chronically-overpriced names; the reverse on the rare chronic under-pricers |
| **F6** | **Delta tilts** | 12-1 momentum; 1-week reversal; post-earnings-announcement drift | Jegadeesh–Titman 1993; Jegadeesh 1990; Bernard–Thomas | Not standalone trades — they *skew* the strike selection of F1/F2 positions, shifting short strikes away from the predicted direction |
| **F7** | **Trend overlay** | Crisis alpha with positive long-run return | [AQR Tail Risk Hedging](https://www.aqr.com/Insights/Research/White-Papers/Tail-Risk-Hedging-Contrasting-Put-and-Trend-Strategies); Hurst–Ooi–Pedersen | Scales the portfolio's net beta-weighted delta target. Replaces the put sleeve |
| **F8** | **Event classifier (LLM)** | Materiality and direction of news; predicted earnings-surprise volatility | — | Veto on entries; a *feature* into F5 and the meta-labeler. Never a trigger on its own |

Factors F1–F4 are direction-agnostic. F5 is direction-agnostic per event. F6–F7 supply the
directional lean. That's the answer to "up and down": not one strategy that predicts both,
but four sources of premium that don't care, plus two tilts that do.

### 3.3 The breadth problem on a small account — stated honestly

Section 2.1 says consistency comes from many independent bets. A $10k account can hold
maybe 8 positions. That tension is real and there's no trick around it — only staging:

| Capital | Factors live | Why |
|---|---|---|
| **$5–10k** | F1, F2, F5 on SPY/QQQ + ~10 liquid names; F7 as a sizing signal only | Cheapest structures; the highest-IC premia; trend expressed through *how much* to sell rather than separate positions |
| **$25–50k** | + F3 calendars, F6 tilts, "mini-dispersion" | Mini-dispersion = short SPY vol vs long vol on 3–5 mega-caps. Crude, but the top 5–7 names are ~30% of the index, so it captures a meaningful slice of F4 |
| **$100k+** | Full set including proper F4 with 15–30 constituents | Real breadth. This is where Sharpe 1.5 becomes plausible |

The Cboe DSPX dispersion index is [documented as institution-only](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-cboe-sp-500-dispersion-index.pdf)
— but it's a fine *signal* for when the correlation premium is rich, and the mini-dispersion
above is retail-executable.

### 3.4 Layer 4 — Meta-labeling (new)

[López de Prado's meta-labeling](https://en.wikipedia.org/wiki/Meta-Labeling) separates two
decisions the current plan conflates: *which side* (the factors decide that) and *whether to
act and how big* (a secondary model decides that). The secondary model is a gradient-boosted
classifier trained on the backtest's own trade outcomes — features are the factor values,
regime probabilities, liquidity, days to earnings, term-structure slope, etc.; the label is
whether the trade hit profit-target before stop (triple-barrier labeling). It's trained
with **purged, embargoed K-fold** so overlapping positions can't leak.

Why it fits here specifically: it's ML applied to tabular features with a binary outcome,
which is the one thing ML reliably does well in finance. It is *not* deep learning on price
series, which has a poor record. And it never picks direction — it only filters and sizes.

Two honest caveats. [QuantConnect's "Why Meta-Labeling Is Not a Silver Bullet"](https://www.quantconnect.com/forum/discussion/14706/why-meta-labeling-is-not-a-silver-bullet/)
is right that it can't rescue a primary signal with no edge; it amplifies a real edge and
does nothing for a fake one. And the frequently-cited "17% → 63% accuracy" toy result is a
toy. The realistic gain is a materially better profit factor from *not taking* the
marginal trades, which is exactly the "steamroller" defense the plan's §5.1 wants.

### 3.5 Layer 5 — Portfolio construction (new, and the biggest upgrade)

The current plan sizes each trade independently with fractional Kelly. The redesign solves a
portfolio each morning:

```
maximize   Σ (expected edge_i × meta-label probability_i × size_i)
subject to net beta-weighted delta  ∈  band set by F7 (trend) and HMM regime
           net vega                 ≤  cap  ×  vol-target scalar
           net gamma                ≥  floor
           per-name, per-sector, per-expiry concentration caps
           max loss per position    ≤  1–2% equity
           buying power             ≤  40%
           realized  σ  target      :  gross exposure × (σ_target / HAR forecast σ)
```

That last line is Moreira–Muir. The vol-target scalar is the mechanism by which the system
automatically gets small before it gets hurt. Strike and expiry per position are chosen
inside this problem by edge-net-of-cost, with 16-delta / 30–45 DTE as the prior.

It's a small quadratic program; `cvxpy` solves it in milliseconds.

### 3.6 Layer 6 — Execution (upgraded)

On a Sharpe-0.67 base strategy, execution alone can be the difference between profitable
and not: the plan's cost model assumes fills 40% into the spread, and on a $2-wide spread
with $0.10 bid-ask per leg, that's $0.16 round-trip against ~$0.60 of credit — 27% of the
edge. Getting fills at 25% instead of 40% is worth more than most signals.

- **Limit-order walker:** quote at mid, step toward the far side on a schedule (e.g., five
  steps over 10 minutes), abandon past a max-slippage threshold set from the position's
  expected edge. Never market orders.
- **Timing:** avoid the first and last 15 minutes; prefer 10:00–11:30 ET and 14:00–15:30 ET
  when spreads are tightest. Vilkov's 0DTE work used a 10:00 ET entry for the same reason.
- **Slippage ledger:** every fill logged against its mid at submission, bucketed by
  liquidity tier and time of day, and fed back into the backtest cost model monthly.
  This is how the backtest stays honest.

### 3.7 Layer 7 — Risk and the learning loop

Everything in the plan's §5 stays (defined risk, drawdown ladder, profit factor ≥ 2.0
target, kill switch). Added:

- **Vol targeting** as a first-class control, per §2.2.
- **IC decay monitoring.** Each factor's rolling 6-month IC is tracked. A factor whose IC
  falls below a threshold is automatically de-weighted in the optimizer, and a factor at
  zero IC for two quarters is retired pending review. Edges decay; the system should
  notice before you do.
- **Regime-attributed P&L.** Monthly report of P&L by factor × HMM regime, so it's visible
  *which* premium is paying and under what conditions.

---

## 4. What I'd keep from the current plan, unchanged

Alpaca for execution and ThetaData for history. The DuckDB/Parquet warehouse with
point-in-time correctness. The single shared code path for backtest and live. Defined risk
on every position. Profit factor and Calmar as the gates, deflated Sharpe and purged CV in
validation, the regime-stratified stress windows, the 40%-into-the-spread cost model, the
3-month paper gate at realistic sizing, and the $5k live floor. All of that was right.

---

## 5. Expected performance, with the sources of each increment

| Stage | Annual Sharpe | Basis |
|---|---|---|
| Mechanical premium selling (current plan's Sleeve A alone) | ~0.6–0.7 | Cboe PUT index, 30 years |
| + volatility targeting | ~0.9–1.1 | Moreira–Muir: +50–100% |
| + multi-factor breadth (F2–F5), meta-labeling filter, execution work | ~1.2–1.5 | Fundamental law; realistic retail ceiling |
| + full dispersion at $100k+ | up to ~1.5–1.8 | Correlation premium is the largest and least beta-exposed |

At Sharpe 1.5: **54% of days positive, 67% of months, 77% of quarters.** At Sharpe 1.0,
which is the more likely first-year outcome: 52.5% / 61% / 69%. A 20% max drawdown remains
the right kill-switch level throughout.

These are not projections. They're the literature's numbers for each component, stacked,
with haircuts. The purpose of the build is to find out whether *this* implementation earns
them.

---

## 6. What changes in the build order, if adopted

The plan's phases 0–3 (setup, data layer, features, backtest engine) stand. Then:

| Phase | Plan | Redesign |
|---|---|---|
| 4 | Sleeve A (mechanical premium selling) | **HAR-IV forecaster + F1**, backtested with and without vol targeting. This single comparison validates §2.2 empirically before anything else is built on it |
| 5 | Risk layer | Risk layer + **portfolio optimizer** (§3.5) |
| 6 | Sleeves B + C | **F2, F5, F7 overlay.** The put sleeve is not built |
| 7–8 | Execution, paper | Same, plus the **slippage ledger** feeding the cost model |
| 9 | Sleeve D | **Meta-labeler**, trained on phases 4–8's trade log |
| 10+ | Live small | Same; **F3, F6, mini-dispersion** added as capital allows |

Timeline is roughly unchanged: 6–8 months to first live dollar. The extra forecasting and
optimization work is offset by *not* building the indicator library or the put sleeve.

---

## Sources

**Benchmarks and premia**
- [Bondarenko, *Historical Performance of Put-Writing Strategies* (Cboe, 2019)](https://cdn.cboe.com/resources/education/research_publications/PutWriteCBOE19_v14_by_Prof_Oleg_Bondarenko_as_of_June_14.pdf)
- [Black & Szado, *Options-Based Benchmark Indexes* (Cboe/Wilshire)](https://cdn.cboe.com/resources/spx/wilshire-options-based-benchmark-indexes-2019.pdf)
- [Israelov & Nielsen, *Still Not Cheap: Portfolio Protection in Calm Markets* (JPM 2015)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2579232)
- [Driessen, Maenhout & Vilkov, *The Price of Correlation Risk: Evidence from Equity Options* (JF 2009)](https://www.researchgate.net/publication/227347954_The_Price_of_Correlation_Risk_Evidence_from_Equity_Options)
- [Ruf, *Limits to Arbitrage and the Skewness Risk Premium in Options Markets*](https://www.bauer.uh.edu/departments/finance/documents/seminars/Ruf_013112.pdf)
- [Gao, Xing & Zhang, *Anticipating Uncertainty: Straddles Around Earnings Announcements* (JFQA 2018)](https://www.ruf.rice.edu/~yxing/straddle_201305_03.pdf)
- [Quantpedia, *Exploiting Term Structure of VIX Futures*](https://quantpedia.com/strategies/exploiting-term-structure-of-vix-futures)
- [Cboe S&P 500 Dispersion Index (DSPX) methodology](https://www.spglobal.com/spdji/en/documents/methodologies/methodology-cboe-sp-500-dispersion-index.pdf)

**Hedging and sizing**
- [AQR, *Tail Risk Hedging: Contrasting Put and Trend Strategies* (2020)](https://www.aqr.com/Insights/Research/White-Papers/Tail-Risk-Hedging-Contrasting-Put-and-Trend-Strategies)
- [Moreira & Muir, *Volatility-Managed Portfolios* (JF 2017)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2659431)
- [Grinold, *The Fundamental Law of Active Management* (1989)](https://blankcapitalresearch.com/learn/grinold-fundamental-law-active-management)

**Forecasting and ML**
- [Corsi, *HAR Models of Realized Volatility* — survey](https://www.researchgate.net/publication/382306092_Heterogeneous_Autoregressive_HAR_Models_of_Realized_Volatility_Specifications_Properties_and_Estimation)
- [Kambouroudis et al., *Forecasting realized volatility: the role of implied volatility…* (JFM 2021)](https://onlinelibrary.wiley.com/doi/full/10.1002/fut.22241)
- [Meta-Labeling — overview](https://en.wikipedia.org/wiki/Meta-Labeling) · [Hudson & Thames toy example](https://hudsonthames.org/meta-labeling-a-toy-example/) · [QuantConnect, *Why Meta-Labeling Is Not a Silver Bullet*](https://www.quantconnect.com/forum/discussion/14706/why-meta-labeling-is-not-a-silver-bullet/)

**What doesn't work**
- [Sullivan, Timmermann & White, *Data-Snooping, Technical Trading Rule Performance, and the Bootstrap* (JF 1999)](https://onlinelibrary.wiley.com/doi/10.1111/0022-1082.00163)
- [Vilkov, *0DTE Strategies* — annotated paper, SPXW 2016–2026](https://github.com/vilkovgr/0dte-strategies/blob/main/docs/paper/paper-annotated.md)
- [Cboe, *Evaluating the Market Impact of SPX 0DTE Options*](https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options/)
