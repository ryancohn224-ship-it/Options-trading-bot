# The scheduled 0DTE condor agent

**Status:** built, runs, paper only. The machinery is finished and tested; the edge is
not proven. Section 6 of [PLAN.md](PLAN.md) is still the gate before any real money.

This is the first working code in the repo. It is deliberately *not* the five-sleeve
system that PLAN.md describes — it is one sleeve, wired end to end, so that the
operational half of the project (scheduling, state, orders, reconciliation, journaling)
gets exercised daily while the research half is still being built.

---

## 0. What it is, in one paragraph

Every weekday a cron slot wakes a short-lived Python process. It checks whether it is
allowed to trade at all, pulls today's SPY option chain, picks a 16-delta iron condor,
runs nine gates over the prices, sizes the position from defined risk, walks a limit
order down to the gated credit, and then hands off to management passes that close on a
profit target, a stop, or the hard 15:45 flatten. Every session — including the ones
where it does nothing — writes a journal entry showing every number it looked at.

## 1. Where authority actually lives

```
  deterministic Python                          the model
  ────────────────────                          ─────────
  is trading allowed?      ── no ──▶ stop
  which strikes?
  is the credit enough?    ── no ──▶ stop
  how many contracts?
  ─────────────────────────────────▶  may I? ──▶ veto ──▶ stop
  place the order          ◀────────  no objection
  manage and exit
  write the numbers        ─────────▶ write the narrative
```

The model can stop a trade and can describe one. It cannot choose a strike, set a size,
move a limit, place an order, or loosen a gate.

The reason is asymmetry, not distrust: a wrong veto costs one day's premium, a wrong
entry costs the width of the spread. Only the cheap error is worth delegating. This is
also why the risk limits live in `config.py` field bounds rather than in a prompt or
even in the YAML — a limit that a language model can talk its way past is not a limit,
and the whole point of the 15:45 flatten is that it fires on the day when every
available argument says to hold on a little longer.

## 2. The daily sequence

| When (ET) | What runs | What it does |
|---|---|---|
| 10:15 | `trading-agent open` | preflight → chain → select → gate → size → review → ladder |
| 10:00–15:45, every 5 min | `trading-agent manage` | profit target, stop, or hold |
| 15:45 | `trading-agent manage` | **force flat**, paying up if it has to |
| 15:50, 15:55 | `trading-agent manage` | retry if the flatten did not fill |
| Sat 08:00 | `trading-agent review` | the weekly read |

Separate processes rather than one long-lived loop: a loop that dies mid-session loses
its position, whereas a cron slot that dies just misses one pass and the next one reads
the position back off disk.

**Why 10:00 and not 09:30.** The first half hour prices the overnight gap, not the day.
**Why 12:30 is the cutoff.** After that there is not enough remaining theta to pay for
the tail risk being carried. **Why 15:45 is absolute.** A 0DTE short that reaches
settlement is not a position, it is an assignment notice.

## 3. The gates

Nine checks, in two groups. All of them run even after one fails, because the journal is
supposed to show the whole picture rather than stop at the first no.

### Risk control — is this structure sane?

| Gate | Rejects |
|---|---|
| `symmetric_wings` | Wings of unequal width — not the trade that was sized |
| `positive_credit` | A short condor that pays a debit at conservative prices |
| `credit_ratio` | Credit below 12% of width: reward too thin for the width being risked |
| `balanced_sides` | A condor whose credit comes almost entirely from one side — that is a directional spread wearing a costume |
| `leg_liquidity` | Any leg unquoted, too wide, or with no size on the side we must trade against |
| `iv_band` | A market too quiet to pay, or too violent to be short into |
| `strike_buffer` | Short strikes pulled inside 0.85 expected moves by a skewed or stale surface |
| `open_gap` | A >1.5% overnight move — today's distribution is not the usual one |

### The edge condition — is there a reason to be here at all?

| Gate | Rejects |
|---|---|
| `variance_risk_premium` | ATM implied vol not at least 15% above 20-day realized |

**This distinction is the most important thing in the document.** A short iron condor is
worth exactly zero in expectation under the risk-neutral measure — that is what pricing
means. No credit threshold changes that. Sell the strikes closer and you collect more
and lose more often, in almost exactly offsetting proportion.

So `credit_ratio` is a reward-to-risk floor, not an edge. It filters out days where the
premium does not justify the operational risk of being in the market. The only
structural reason to be short premium is the one PLAN.md §0 names: implied volatility
has historically printed above subsequently realized volatility, and that premium is
direction-agnostic. `variance_risk_premium` is the gate that actually expresses the
thesis, and it is the one to be suspicious of, because a 20-day close-to-close estimate
is a crude proxy for the realized vol of the next six hours.

**None of this is validated.** The VRP is well documented at 30–45 days. Whether it
survives at 0DTE, after four legs of bid/ask, is an open question this repo has not
answered. Until the backtest in PLAN.md §6 exists, this agent is an execution harness
with a plausible entry rule, running in paper. Treat its P&L as a plumbing test, not as
evidence.

## 4. Sizing

Size comes from max loss, never from credit and never from a contract count anything
suggested:

```
max loss per contract = (wing width − net credit) × 100
contracts             = floor(min(2% of equity, buying power headroom) / max loss)
                        capped at 10
```

Zero contracts is a valid answer. On a $1,000 account, one $1-wide condor risks ~$87,
which is 8.7% of equity — so the agent declines, which is the correct behaviour and the
same conclusion PLAN.md §2 reaches from the other direction. The `min_equity: 5000`
floor stops the session before any of that arithmetic runs.

## 5. Traps this design is built around

Every one of these is a bug that produces plausible-looking behaviour rather than a
crash, which is why they are handled structurally rather than left to be noticed.

1. **The credit sign.** Alpaca expresses a multi-leg credit as a *negative* limit price.
   Backwards, you pay to open a short condor, and a paper account fills it happily. It
   has [its own test](../agent/tests/test_broker.py).
2. **Strike scaling in OCC symbols.** `604.0 → 00604000`. Get it wrong and you get a
   symbol that is valid and is the wrong contract. Parsed from the right so root length
   never shifts the field.
3. **Server timezone.** All session times are exchange-local. A UTC box would otherwise
   shift the entry window by an hour twice a year.
4. **Missing greeks.** The free indicative feed does not reliably publish delta. Falling
   back to "whatever strike is a few points out" silently turns a 16-delta condor into a
   30-delta condor on a high-vol morning, so delta is recomputed from IV, and from the
   mid price if IV is missing too.
5. **The worthless long wing.** Late in the day a far wing's bid decays to nothing.
   Requiring a two-sided market on all four legs to compute an exit strands the *short*
   legs into settlement. Shorts need an offer; longs are valued at zero if bidless.
6. **A ladder that concedes past its own gate.** Walking the limit below the gated credit
   is not patience, it is entering a different trade from the one that was approved. The
   ladder is floored at the gate price.
7. **The journal overwriting itself.** Entry and exit are separate processes on the same
   date. The daily page is rebuilt from every row carrying that date, so the afternoon's
   exit cannot erase the morning's reasoning.
8. **Kill switches that outlive their reason — or don't.** A daily-loss halt expires with
   its day. A halt a human set stays set until a human clears it.
9. **Rolling the day more than once.** Every cron invocation rolls the state; only the
   first of the day may count anything, or a losing streak inflates by one per cron slot.
10. **A failed force-flat going quiet.** Past 15:45 with an open structure is the one
    "hold" that writes an escalation into the journal and surfaces in the weekly review.

## 6. Running it

```bash
cd agent
python -m venv .venv && .venv/bin/pip install -e '.[broker,dev]'

.venv/bin/pytest                                   # 93 tests, no network, no keys
.venv/bin/trading-agent --config config/agent.yaml demo
```

`demo` runs a whole session against a Black-Scholes-priced synthetic chain — entry,
management passes, exit, journal — with no credentials and at any time of day. It is the
thing to run after changing anything.

Against the real paper account:

```bash
cp .env.example .env      # Alpaca paper keys
set -a && . ./.env && set +a
.venv/bin/trading-agent --config config/agent.yaml --broker alpaca --source alpaca preflight
```

Then `crontab ops/crontab.example`. To run it as a Claude Code routine instead, see
[ops/claude-routine.md](../agent/ops/claude-routine.md).

**Stopping it:** `touch agent/var/state/KILL_SWITCH`. Checked before anything else, every
run.

**Going live** takes two independent unlocks — `mode: live` in the YAML *and*
`TRADING_AGENT_ALLOW_LIVE` set to an exact phrase in the environment. Neither alone does
anything. Do not set either until §7 is satisfied.

## 7. What has to happen before real money

1. **A backtest.** PLAN.md §6, on history that contains at least two genuine regime
   breaks. Right now the entry rule is a hypothesis with a test suite, which is not the
   same thing as an edge.
2. **Sixty-plus paper sessions**, judged on whether the gates behave sensibly and how
   often it stands down — not on P&L, which at this sample size is noise.
3. **Slippage measured against model.** The simulator fills at conservative prices on
   purpose. If live fills come in worse than that, the gate thresholds are wrong.
4. **A reconciliation loop.** PLAN.md §4.4 rule 4: compare internal state against the
   broker's positions on a schedule and halt on a mismatch. The agent persists its own
   position and would currently not notice a divergence. This is the largest known gap.

## 8. Known gaps

- No reconciliation against broker positions (see above).
- Partial fills on a four-leg order are treated as unfilled and cancelled; a partial that
  fills *and then* the cancel races would leave a naked leg. Alpaca fills mleg orders
  atomically as far as documented, but this has not been verified under load.
- Realized vol is a 20-day close-to-close estimate. Parkinson or Yang-Zhang on intraday
  ranges would be a better comparison for a six-hour holding period.
- The `min_atm_iv`/`max_atm_iv` band is asserted, not fitted.
- One underlying, one structure, one entry per day.
