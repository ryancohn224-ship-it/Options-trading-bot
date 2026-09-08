# The research loop

**What it is:** a daily cycle that runs the strategy in paper, stores everything the
market offered, replays competing parameter sets over that history, and changes the
live configuration only when a challenger survives a pre-registered test on data the
search never saw.

**What it is not:** a process that tunes parameters until the numbers look good. That
process always succeeds, which is exactly the problem with it.

---

## 1. The problem this is built around

The agent takes at most one trade a day. Ask what that means for tuning:

At the observed spread of P&L for a 0DTE condor, distinguishing a real edge from zero
takes roughly **200–400 trades**. At one a day that is one to two years. Any conclusion
drawn from twenty sessions — "the stop is too tight", "it should skip Mondays", "0.16
delta is better than 0.12" — is drawn from twenty data points with a signal-to-noise
ratio near zero.

The trap is that tuning on twenty points *works*. You will find a parameter set that
would have made money over those twenty days, because with enough parameters one always
exists. It will have no out-of-sample edge whatsoever, and there is nothing about the
experience of finding it that feels like a mistake. It feels like doing the work.

So the loop is built so that the honest answer is reachable, including the answer that
there is nothing here.

## 2. Stored chains, not stored trades

The unit of evidence is the **chain**, not the trade.

Every time the agent looks at the market — at entry and at every management pass — the
entire option chain is written to `var/snapshots/<date>.jsonl.gz`. About 100 KB a day,
committed to git.

That changes the arithmetic completely:

- A parameter change is re-run against **every day already stored**, in about a second.
- A variant invented in week nine gets **eight weeks of evidence the moment it exists**.
- The scarce resource stops being market days and becomes *ideas worth testing*.

`replay.py` runs the identical `select_condor`, `run_gates`, `size_condor` and
`decide_exit` calls the live agent runs. A variant cannot score well in research by
taking a path the live agent would not take, because there is only one implementation
of the path.

## 3. Fidelity: what counts as evidence

| Tier | Source | Bid/ask | May promote? |
|---|---|---|---|
| `live` | The OPRA feed, recorded as the agent traded | Real | **Yes** |
| `backfill` | Reconstructed from Alpaca's historical bars | **Modelled** | No |
| `synthetic` | `simulate.py` | **Modelled** | No |

Alpaca publishes historical option *bars and trades*, but no historical *quotes*. Since
the credit gate runs entirely on bid/ask, a backfill would need a modelled spread — and
that model would quietly determine whether the gate passes, which means it would
determine the answer. Same for synthetic days.

So both are for generating hypotheses and proving the machinery. `check_promotion`
counts only `live` trades, and the report says so on every page that shows anything
else.

## 4. The holdout

25% of days are held out, assigned by `sha256(date) % 100 < 25`.

Deliberately not random and not "the last N days":

- A **random** split reshuffles every run, so a variant can be re-rolled until it wins.
- A **recent-days** split lets the optimiser watch the holdout fill up and shape
  proposals to fit it.
- A **date hash** fixes each day's role permanently, before the data existed. A variant
  written today is tested against holdout days from months ago that nobody chose.

The holdout is consumable. Each opening leaks a little information into the search, so
every one is logged and the report counts them. After about **ten**, it has been
optimised against and must be recut against days that have arrived since.

## 5. Pre-registration

`trading-agent propose` records the hypothesis and the success criterion *before*
`promote` opens the holdout.

A hypothesis written afterwards is unfalsifiable: the result is already known, and the
reasoning arrives to fit it. Writing down "this fails if the edge is under 0.02R" in
advance is what makes a negative result possible to recognise.

The criterion must include what would make the idea **wrong**. A proposal with no
failure condition is not a proposal.

## 6. Promotion

A challenger replaces the champion only when all of these hold:

| Requirement | Default |
|---|---|
| Live-fidelity research trades | ≥ 40 |
| Edge over champion, research days | ≥ +0.02R per trade |
| Live-fidelity holdout trades | ≥ 12 |
| Holdout expectancy | > 0 |
| Holdout openings so far | < 10 |

Every criterion is evaluated and reported, never short-circuited at the first failure —
a rejection naming one reason invites fixing that one thing and asking again, which is
overfitting to the promotion rule itself.

Variants are compared on **R** (P&L over capital at risk), not dollars. A wider-winged
variant risks more per contract, so raw dollars would flatter it for no reason except
size. Sizing uses a fixed notional equity rather than a compounding balance, because
compounding conflates edge with the order the trades happened to arrive in.

## 7. What the report will not let you get away with

`var/reports/<date>.md`, every trading day:

- **Sample size before performance.** A win rate is never shown without the number of
  trades it would take to mean something.
- **The best-of-K correction.** Ranking eleven variants and reporting the winner is
  eleven chances to be lucky. `best_of_k_pvalue` resamples the observed distribution
  around a zero mean, takes the best of eleven each time, and reports how often pure
  noise produces a leaderboard this good. Early on the answer is usually *most of the
  time*, and the report says so in those words.
- **A confidence interval on expectancy**, by bootstrap rather than a t-interval,
  because condor P&L is bounded and sharply skewed.
- **Fidelity labels everywhere**, and a banner when nothing stored can change anything.

## 8. Costs are modelled, because they decide this trade

Alpaca charges no commission on options, but OCC clearing, the options regulatory fee
and exchange fees pass through at roughly **$0.065 per contract per transaction**. A
condor is four legs in and four legs out: **eight transactions, ~$0.52 per contract
round trip**.

Against a $1-wide condor collecting $13 per contract, that is 4% of gross before a
single spread is crossed — and the spread is the larger cost. Replay charges these on
every trade. A backtest that does not is not measuring the strategy.

## 9. Stopping

The loop is allowed to conclude that the strategy does not work, and should:

- **No edge:** 150+ live trades, no variant's expectancy interval excludes zero, best
  point estimate below costs. Report it and stop. This is a successful experiment with
  a negative result, not a failed loop.
- **Holdout exhausted:** ten openings. Stop promoting; recut.
- **Drift:** more than one promotion a month means the search is chasing noise.

## 10. What the loop has already found

Both of these came out of the machinery on its first run, before any live data existed.

**The stop was inside the bid/ask spread.** The exit rules compared a *marketable* cost
to close against a threshold set as a multiple of the *credit received* — both prices
spread-crossed, in opposite directions. On a four-leg structure the cost to close
started a full round trip above the credit, which put a "2× credit" stop below where
the position opened. Every trade stopped out seconds after entry; the backtest reported
a 0% win rate rather than an error. Fixed in `exits.py`: **decisions on the mid mark,
orders and P&L at marketable prices**, with the stop expressed as a fraction of max
loss rather than a multiple of a spread-crossed credit. Win rate went 29% → 66%.

**Narrow wings may not clear their own costs.** Eight leg-crossings cost the same at any
wing width, while credit scales with width. Across simulated spread regimes, $1 wings
were negative at every level tested and $2–3 wings were the first to clear. The
mechanism is arithmetic and does not depend on the vol model — but the magnitudes came
from synthetic days, so **the default was not changed**. It is pre-registered as the
loop's first proposal, to be settled on live data through the normal promotion gate.

That second one is the discipline working. The evidence was suggestive, the mechanism
was plausible, and it still does not get to change the live configuration until it
survives a test it could have failed.
