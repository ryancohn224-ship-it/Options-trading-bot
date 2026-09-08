# Daily loop — operating procedure

You are running a systematic 0DTE iron condor agent on a **paper** account and looking
for genuine improvements to it. Run this at the end of each trading day.

Repository: `agent/`. All commands assume you are in that directory with the venv
active. The full design is in `../docs/AGENT.md`; the research discipline is in
`../docs/RESEARCH.md`. Read those before your first run.

---

## The rules that do not bend

These exist because the failure mode of this task is not losing money — it is
*convincing yourself you have found something*. That failure feels exactly like
diligence from the inside, so it has to be blocked procedurally rather than noticed.

1. **Never change a live parameter because of recent P&L.** Not after a bad day, not
   after a good week. Parameters move only through `promote`, which requires 40
   live-fidelity research trades, a positive holdout, and a logged decision.
2. **Never widen a risk limit.** `max_risk_pct`, `max_daily_loss_pct`,
   `max_consecutive_loss_days`, `min_equity`, `force_flat` and the two live-trading
   locks are not optimisation targets. If a change needs one of them loosened, the
   change is rejected.
3. **Never edit `agent.yaml` directly.** Ideas become *variants*, variants get
   replayed, winners get promoted. That is the only path.
4. **Pre-register before you look.** `propose` writes the hypothesis and the success
   criterion down first. A hypothesis written after seeing the result is not a
   hypothesis.
5. **Synthetic and backfilled days can never promote anything.** They are for
   generating ideas and testing the machinery. Only real bid/ask days are evidence.
6. **Standing down is a result.** A day with no trade is data about the gates, not a
   problem to fix. If the fix for "we didn't trade" is "loosen the gate", stop.
7. **Report what happened, including nothing happening.** Do not manufacture a finding
   to make the report feel worthwhile. "No change, 3 more trades logged, still 34
   short of a verdict" is a complete and correct report.

---

## Every trading day

```bash
trading-agent --config config/agent.yaml --broker alpaca --source alpaca report
```

Cron has already run `open` and `manage` through the session (see
`ops/crontab.example`); this step reads what happened and scores it. If cron is not
installed, run the session steps yourself first — `preflight`, then `open`, then
`manage` every five minutes until flat or past 15:45.

Then:

1. **Read the report.** It is written to `var/reports/<date>.md`.
2. **Check for escalations.** Any session with outcome `error` — especially a failed
   force-flat — is the first thing you look at and the first thing you tell the user.
   An open 0DTE structure past 15:45 is urgent.
3. **Sanity-check the day against the journal.** If the agent traded, does the exit
   reason make sense? If it stood down, which gate was closest to passing?
4. **Commit the day.** `var/` is version-controlled on purpose — it is how the loop
   remembers across sessions:
   ```bash
   git add var/ && git commit -m "session: <date>"
   ```
5. **Report to the user** in the shape below.

Nothing else. Most days end here.

---

## When there is enough data to say something

Only once the report stops saying *insufficient* — 30+ live-fidelity trades on a
variant — does analysis become meaningful. Then, at most **once a week**:

1. **Look at the leaderboard** (`trading-agent replay`). Note that the holdout column
   is hidden; that is deliberate.
2. **Ask what mechanism would explain the ordering.** A variant that leads for no
   articulable reason is noise. Write the mechanism down or drop the idea.
3. **Check the best-of-K line in the report.** If it says a leaderboard this good
   happens 20%+ of the time by chance, you have not found anything.
4. **Pre-register** anything worth pursuing:
   ```bash
   trading-agent propose --variant wider-wings \
     --hypothesis "Eight leg-crossings cost the same at any width, so credit-to-cost
                   improves with wider wings." \
     --criterion  "Beats champion by >= 0.02R on research days and is positive on holdout."
   ```
5. **Only then** open the holdout:
   ```bash
   trading-agent promote --variant wider-wings --reason "pre-registered 2026-09-15"
   ```
   This logs the look. The holdout is good for about ten openings before it has been
   optimised against and needs recutting. Spend them carefully.
6. If approved, `--apply`, then mirror the winning overrides into `agent.yaml` and
   commit both. Tell the user what changed and why, with the numbers.

## Adding a new idea

New variants are cheap and are the main way this improves — each one gets scored
against *every day already stored*, so an idea written in week nine inherits eight
weeks of evidence immediately. Add it to `config/variants.yaml` with a `note` saying
what it is testing and what would make it wrong, then `trading-agent replay`.

Good ideas come from a mechanism: something about spreads, decay, skew, the shape of
the fee schedule, an observed pattern in *which gate* keeps failing. Bad ideas come
from scanning parameters for one that would have helped.

## Retiring a variant

After 40+ trades, a variant clearly behind the champion with no mechanism to explain a
turnaround gets `status: retired` and a `retired_reason`. It stays in the file — a
retired variant is a recorded negative result, and re-testing it later without knowing
it already failed is how you go in circles.

---

## Stopping conditions

Say so plainly when any of these is reached. Do not keep grinding.

- **No edge.** 150+ live trades across the variant set, no variant's expectancy
  interval excludes zero, best point estimate below costs. The honest report is: this
  does not work, here is what it cost to find out. That is a successful outcome of the
  experiment, not a failure of the loop.
- **Holdout exhausted.** 10 openings. Stop promoting, recut against fresh days.
- **Drifting.** More than one promotion a month means the search is chasing noise.
- **The user's floor.** Cumulative paper drawdown past what they said they would
  tolerate live — stop and ask, do not trade through it.

---

## What to send the user, daily

Keep it short. Lead with the number, then the state, then anything that needs them.

```
0DTE condor — <date>

Today:      <traded / stood down (which gate) / halted (why)>
P&L:        <$X>   |  Week: <$Y>  |  Since <start>: <$Z> over <N> trades
Position:   flat
Data:       <N> live sessions stored, <M> trades

Verdict:    <one line from the report — usually "not enough data yet, need N more">
Leaderboard: <champion's rank, and the top challenger if it has 30+ trades>

Needs you:  <nothing / an escalation / a decision>
```

Rules for the report itself:

- **Lead with what they asked for**: today's P&L and the running total.
- **Never present a small sample as a finding.** If it is under 30 trades, say so in
  the same sentence as the number.
- **Escalations go at the top**, not in their normal slot.
- **If nothing happened, say nothing happened.** Do not pad.
- Never promise or project a return. Report what occurred.
