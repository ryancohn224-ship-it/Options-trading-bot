"""Circuit breakers with persisted state: daily halt, monthly size cut, drawdown kill switch, consecutive-loss
sleeve pause, and the day-trade guard. Pure functions over a small state dict so backtest and live share them."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from ..config import Cfg


@dataclass
class RiskState:
    high_water: float = 0.0
    day_start_equity: float | None = None
    day_start_date: str | None = None
    month_start_equity: float | None = None
    month_key: str | None = None
    consecutive_losses: int = 0
    day_trades: list[str] = field(default_factory=list)  # ISO dates of same-day round trips
    killed: bool = False
    kill_reason: str | None = None
    halted_today: str | None = None

    @classmethod
    def load(cls, path: Path) -> RiskState:
        if path.exists():
            return cls(**json.loads(path.read_text()))
        return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))


@dataclass
class RiskDecision:
    allow_new_entries: bool
    size_mult: float
    flatten: bool
    reasons: list[str]


def evaluate(state: RiskState, cfg: Cfg, equity: float, today: date) -> RiskDecision:
    r = cfg.risk
    reasons: list[str] = []
    # roll day / month anchors
    t = today.isoformat()
    if state.day_start_date != t:
        state.day_start_date, state.day_start_equity, state.halted_today = t, equity, None
    mk = today.strftime("%Y-%m")
    if state.month_key != mk:
        state.month_key, state.month_start_equity = mk, equity
    state.high_water = max(state.high_water, equity)

    if state.killed:
        return RiskDecision(False, 0.0, True, [f"KILLED: {state.kill_reason}"])
    dd = 1 - equity / state.high_water if state.high_water > 0 else 0.0
    if dd >= r.max_drawdown_kill_pct:
        state.killed, state.kill_reason = (
            True,
            f"drawdown {dd:.1%} >= {r.max_drawdown_kill_pct:.0%}",
        )
        return RiskDecision(False, 0.0, True, [state.kill_reason])
    allow, mult = True, 1.0
    if state.day_start_equity and equity < state.day_start_equity * (1 - r.daily_loss_halt_pct):
        allow = False
        state.halted_today = t
        reasons.append("daily loss halt")
    if state.month_start_equity and equity < state.month_start_equity * (
        1 - r.monthly_loss_halve_pct
    ):
        mult *= 0.5
        reasons.append("monthly loss: size halved")
    if state.consecutive_losses >= r.consecutive_loss_pause:
        allow = False
        reasons.append(f"{state.consecutive_losses} consecutive losses: paused")
    return RiskDecision(allow, mult, False, reasons)


def record_close(state: RiskState, pnl: float, open_date: date, close_date: date) -> None:
    state.consecutive_losses = state.consecutive_losses + 1 if pnl < 0 else 0
    if open_date == close_date:
        state.day_trades.append(close_date.isoformat())


def day_trades_in_window(state: RiskState, today: date, days: int = 5) -> int:
    cutoff = today - timedelta(days=7)  # 5 business days ≈ 7 calendar
    return sum(1 for d in state.day_trades if date.fromisoformat(d) > cutoff)


def can_close_today(state: RiskState, cfg: Cfg, open_date: date, today: date) -> bool:
    if not cfg.risk.enforce_day_trade_guard or open_date != today:
        return True
    return day_trades_in_window(state, today) < cfg.risk.max_day_trades_per_5d
