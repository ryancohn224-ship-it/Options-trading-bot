# agent — the scheduled 0DTE iron condor

The first working code in this repo. A short-lived process wakes on a schedule, decides
whether the market is offering a trade worth doing, and either takes it or writes down
why it didn't.

Full design notes and the honest caveats: **[../docs/AGENT.md](../docs/AGENT.md)**.
The daily improvement loop, and why it is built to resist its own conclusions:
**[../docs/RESEARCH.md](../docs/RESEARCH.md)**.

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -e '.[broker,dev]'
.venv/bin/pytest                                          # no network, no keys
.venv/bin/trading-agent --config config/agent.yaml demo   # a whole synthetic session

# Watch the research loop run, with no keys and no market:
.venv/bin/trading-agent --config config/agent.yaml simulate --days 150
.venv/bin/trading-agent --config config/agent.yaml replay
.venv/bin/trading-agent --config config/agent.yaml report
```

## Commands

| | |
|---|---|
| `preflight` | What would stop it trading right now |
| `open` | One entry attempt |
| `manage` | One management pass — target, stop, or the 15:45 flatten |
| `demo` | A full session against a synthetic chain, no credentials |
| `review` | What the journal says about the last N days |
| `show` | Render the most recent session |
| `report` | End-of-day report: today, leaderboard, statistical verdict |
| `replay` | Score every variant over every stored chain |
| `variants` | List the champion and its challengers |
| `propose` | Pre-register a hypothesis before testing it |
| `promote` | Open the holdout and decide on a challenger (logged) |
| `simulate` | Generate synthetic sessions to exercise the loop |

Global flags go before the subcommand: `--config`, `--broker sim|alpaca`,
`--source synthetic|alpaca`, `--equity`, `--spot`, `--iv`.

## Layout

```
config/agent.yaml        strategy parameters (bounded by code, hashed into every entry)
ops/                     crontab and the Claude Code routine
src/trading_agent/
  config.py              Pydantic schema — this is where the risk limits live
  clock.py               exchange-local session timing
  marketdata.py          Alpaca chain + a Black-Scholes synthetic chain
  condor.py              strike selection and the nine gates
  sizing.py              contracts from defined risk
  guardrails.py          preflight: kill switch, streaks, windows, equity floor
  broker.py              Alpaca multi-leg adapter + a pessimistic simulator
  llm.py                 the model's veto-only seat
  exits.py               when to get out - shared by live and replay
  session.py             the daily sequence
  journal.py             the output that actually matters
  snapshot.py            stored chains: the research asset
  replay.py              re-run any variant over any stored day
  variants.py            named parameter overlays that compete
  research.py            statistics, the holdout, promotion rules
  report.py              the end-of-day report
  simulate.py            a synthetic market, to prove the loop
tests/                   168 tests
var/                     state, journal, snapshots, research - COMMITTED (the loop's memory)
```

## Safety

- Paper by default. Live needs `mode: live` **and** `TRADING_AGENT_ALLOW_LIVE` set to an
  exact phrase — two independent unlocks.
- `touch var/state/KILL_SWITCH` stops everything, checked first on every run.
- The edge is unproven. See [../docs/AGENT.md §7](../docs/AGENT.md#7-what-has-to-happen-before-real-money).
