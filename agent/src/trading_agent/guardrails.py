"""Preflight checks that run before anything looks at a price.

These are the checks that have nothing to do with whether the trade is good and
everything to do with whether the agent is allowed to trade at all. They are ordinary
Python reading ordinary state files, because a limit that only exists in a prompt is
not a limit — it is a suggestion the model can be argued out of.

Live trading additionally requires an environment variable set outside this repo.
Flipping `mode: live` in YAML is not sufficient, on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .clock import Clock
from .config import AgentConfig
from .models import Account, GateReport, GateResult
from .state import AgentState

LIVE_ENV_VAR = "TRADING_AGENT_ALLOW_LIVE"
LIVE_ENV_VALUE = "yes-i-have-read-the-risk-section"


@dataclass(frozen=True)
class Preflight:
    report: GateReport
    halted: bool

    @property
    def ok(self) -> bool:
        return self.report.passed


def preflight(
    config: AgentConfig,
    clock: Clock,
    state: AgentState,
    account: Account | None,
    env: dict[str, str],
    broker_says_open: bool | None = None,
    holding_position: bool = False,
) -> Preflight:
    results: list[GateResult] = []
    halted = False

    if config.mode == "live":
        allowed = env.get(LIVE_ENV_VAR) == LIVE_ENV_VALUE
        results.append(
            GateResult(
                "live_authorisation",
                allowed,
                "live mode confirmed by environment"
                if allowed
                else f"config says live but {LIVE_ENV_VAR} is not set to the required value",
            )
        )
    else:
        results.append(GateResult("live_authorisation", True, "paper mode"))

    if state.kill_switch:
        halted = True
    results.append(
        GateResult(
            "kill_switch",
            not state.kill_switch,
            state.kill_switch or "clear",
        )
    )

    if config.paths.kill_switch_file.exists():
        halted = True
        results.append(
            GateResult(
                "manual_halt",
                False,
                f"{config.paths.kill_switch_file} exists — delete it to resume",
            )
        )
    else:
        results.append(GateResult("manual_halt", True, "no manual halt file"))

    consecutive_ok = state.consecutive_loss_days < config.risk.max_consecutive_loss_days
    if not consecutive_ok:
        halted = True
    results.append(
        GateResult(
            "loss_streak",
            consecutive_ok,
            f"{state.consecutive_loss_days} losing day(s) in a row "
            f"(halt at {config.risk.max_consecutive_loss_days}; needs a human to clear)",
        )
    )

    daily_floor = -abs(config.risk.max_daily_loss_pct) * (account.equity if account else 0.0)
    within_daily = account is None or state.realized_pnl_today > daily_floor
    if not within_daily:
        halted = True
    results.append(
        GateResult(
            "daily_loss",
            within_daily,
            f"realized {state.realized_pnl_today:+.0f} today against a "
            f"{daily_floor:.0f} floor",
        )
    )

    trading_day = broker_says_open if broker_says_open is not None else clock.is_probably_trading_day()
    results.append(
        GateResult(
            "market_open",
            bool(trading_day),
            "market open today"
            if trading_day
            else f"{clock.today().isoformat()} is not a full trading session",
        )
    )

    results.append(
        GateResult(
            "half_day",
            not clock.is_probably_half_day(),
            "full session" if not clock.is_probably_half_day() else "early close; standing down",
        )
    )

    results.append(
        GateResult(
            "entry_window",
            clock.in_entry_window(),
            f"{clock.now():%H:%M:%S %Z} against "
            f"{config.schedule.entry_start:%H:%M}-{config.schedule.entry_cutoff:%H:%M}",
        )
    )

    results.append(
        GateResult(
            "entries_today",
            state.entries_today < config.risk.max_entries_per_day,
            f"{state.entries_today} of {config.risk.max_entries_per_day} used",
        )
    )

    results.append(
        GateResult(
            "flat",
            not holding_position,
            "no open structure" if not holding_position else "already holding a condor",
        )
    )

    if account is None:
        results.append(GateResult("min_equity", False, "no account snapshot available"))
    else:
        results.append(
            GateResult(
                "min_equity",
                account.equity >= config.risk.min_equity,
                f"equity {account.equity:,.0f} against floor {config.risk.min_equity:,.0f}",
            )
        )

    return Preflight(GateReport(tuple(results)), halted)


def check_daily_loss(state: AgentState, account: Account, config: AgentConfig) -> str | None:
    """Called after every realized P&L update. Returns a kill-switch reason or None."""
    floor = -abs(config.risk.max_daily_loss_pct) * account.equity
    if state.realized_pnl_today <= floor:
        return (
            f"daily_loss: realized {state.realized_pnl_today:+.0f} breached the "
            f"{floor:.0f} floor on {date.today().isoformat()}"
        )
    return None
