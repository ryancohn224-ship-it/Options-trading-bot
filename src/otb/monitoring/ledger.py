"""Trade log, slippage ledger, and equity curve as Parquet; metrics computed from them."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from ..strategy.types import Position


class Ledger:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.equity: list[tuple[date, float, float]] = []  # date, equity, scalar
        self.slips: list[dict] = []
        self.events: list[dict] = []

    def record_equity(self, d: date, equity: float, scalar: float) -> None:
        self.equity.append((d, equity, scalar))

    def record_slip(
        self, d: date, pos_id: str, action: str, mid: float, fill: float, qty: int, n_legs: int
    ) -> None:
        self.slips.append(
            {
                "date": d.isoformat(),
                "pos": pos_id,
                "action": action,
                "mid": mid,
                "fill": fill,
                "qty": qty,
                "slip_per_share": (mid - fill) if action == "open" else (fill - mid),
                "n_legs": n_legs,
            }
        )

    def log(self, d: date, msg: str) -> None:
        self.events.append({"date": d.isoformat(), "msg": msg})

    def save(self, positions: list[Position]) -> None:
        pl.DataFrame(
            [p.to_dict() | {"legs": str(p.legs), "meta": str(p.meta)} for p in positions]
        ).write_parquet(self.root / "trades.parquet") if positions else None
        if self.equity:
            pl.DataFrame(
                self.equity, schema=["date", "equity", "vol_scalar"], orient="row"
            ).write_parquet(self.root / "equity.parquet")
        if self.slips:
            pl.DataFrame(self.slips).write_parquet(self.root / "slippage.parquet")
        if self.events:
            pl.DataFrame(self.events).write_parquet(self.root / "events.parquet")


def metrics(equity: list[tuple[date, float, float]], positions: list[Position]) -> dict:
    if not equity:
        return {}
    e = np.array([x[1] for x in equity], dtype=float)
    r = np.diff(e) / e[:-1]
    n = len(r)
    ann = 252
    total_ret = e[-1] / e[0] - 1
    years = max(n / ann, 1e-9)
    cagr = (e[-1] / e[0]) ** (1 / years) - 1 if years > 0 else 0.0
    vol = r.std() * np.sqrt(ann) if n > 1 else 0.0
    sharpe = (r.mean() / r.std() * np.sqrt(ann)) if n > 1 and r.std() > 0 else 0.0
    dn = r[r < 0]
    sortino = (r.mean() * ann / (dn.std() * np.sqrt(ann))) if len(dn) > 1 and dn.std() > 0 else 0.0
    hw = np.maximum.accumulate(e)
    dd = 1 - e / hw
    maxdd = float(dd.max())
    calmar = cagr / maxdd if maxdd > 0 else 0.0
    closed = [p for p in positions if p.status == "closed" and p.realized_pnl is not None]
    wins = [p.realized_pnl for p in closed if p.realized_pnl > 0]
    losses = [-p.realized_pnl for p in closed if p.realized_pnl <= 0]
    pf = (
        (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (float("inf") if wins else 0.0)
    )
    reasons = {}
    for p in closed:
        reasons[p.close_reason] = reasons.get(p.close_reason, 0) + 1
    return {
        "days": int(n),
        "start_equity": float(e[0]),
        "end_equity": float(e[-1]),
        "total_return": float(total_ret),
        "cagr": float(cagr),
        "ann_vol": float(vol),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_drawdown": maxdd,
        "calmar": float(calmar),
        "n_trades": len(closed),
        "win_rate": (len(wins) / len(closed)) if closed else 0.0,
        "avg_win": float(np.mean(wins)) if wins else 0.0,
        "avg_loss": float(np.mean(losses)) if losses else 0.0,
        "profit_factor": float(pf),
        "pct_positive_days": float(np.mean(r > 0)) if n else 0.0,
        "worst_day": float(r.min()) if n else 0.0,
        "close_reasons": reasons,
    }
