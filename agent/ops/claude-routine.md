# Running the agent as a Claude Code routine

The cron setup in `crontab.example` is the deployment that needs nothing but a box.
This is the alternative: let Claude Code drive the same three commands on a schedule,
so the daily narrative is written by the model that watched the session happen.

The division of labour does not change. Claude runs the commands and writes prose. It
does not choose strikes, set size, or place orders — `trading-agent` does all of that
in Python, and the risk limits live in code where a prompt cannot reach them.

## Routine prompt

> Run the 0DTE condor agent for today.
>
> 1. `cd agent && trading-agent --config config/agent.yaml --broker alpaca --source alpaca preflight`
>    Read the output. If it says standing down, stop here and tell me the reason in one line.
> 2. If preflight is clear, run `... open`.
> 3. Every 5 minutes until 15:50 ET, run `... manage`. Stop early if it reports the
>    position is closed.
> 4. Read today's page in `agent/var/journal/` and add a short section under `## Notes`
>    covering: what the market offered, which gate was closest to failing, and anything
>    a human should look at. Do not change any other part of the file.
> 5. Commit `agent/var/journal/` and `agent/var/state/` with the message
>    `journal: <date>`.
>
> Never edit `config/agent.yaml` and never place an order by any other means. If the
> agent stands down, that is a result, not a problem to solve.

## Scheduling it

```
claude routine create --name "0dte-condor" --cron "15 10 * * 1-5" --prompt-file ops/claude-routine.md
```

Or non-interactively from cron, which is the same thing with fewer moving parts:

```
15 10 * * 1-5 cd /opt/options-trading-bot && claude -p "$(cat agent/ops/claude-routine.md)"
```

## The in-process alternative

Setting `llm.enabled: true` in `agent.yaml` gives the model a narrower role inside the
Python process: it reviews each already-approved trade and may veto it. See
`agent/src/trading_agent/llm.py` for exactly what it is and is not allowed to do.

The two can run together. The routine above is the narrator; `llm.enabled` is the
reviewer. Neither is ever the trader.
