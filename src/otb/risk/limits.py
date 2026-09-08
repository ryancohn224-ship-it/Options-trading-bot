"""Position management rules (profit-take, stop, DTE exit, expiry) evaluated against current marks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from ..config import Cfg
from ..strategy.types import Position


@dataclass
class ExitSignal:
    position: Position
    reason: str
    close_debit_mid: float  # per share, positive = we pay


def current_debit_to_close(p: Position, chain: pl.DataFrame) -> float | None:
    """Cost per share to close: buy back shorts at ask-ish, sell longs at bid-ish → use mid here; the broker
    applies the fill model."""
    q = chain.select(["symbol", "mid"]).to_dict(as_series=False)
    idx = dict(zip(q["symbol"], q["mid"]))
    total = 0.0
    for leg in p.legs:
        m = idx.get(leg["symbol"])
        if m is None:
            return None
        total += (
            leg["side"] * m * -1
        )  # closing a short (side -1) means buying: +mid ; closing a long: -mid
    return total


def check_exits(
    positions: list[Position], chain: pl.DataFrame, cfg: Cfg, today: date
) -> list[ExitSignal]:
    sc = cfg.structure
    out = []
    for p in positions:
        if p.status != "open":
            continue
        dte = (p.expiry - today).days
        debit = current_debit_to_close(p, chain)
        if debit is None:
            if dte <= 0:
                out.append(ExitSignal(p, "expiry_no_quote", 0.0))
            continue
        ref = p.entry_mid if p.entry_mid > 0 else p.credit_received
        pnl_per_share = (
            ref - debit
        )  # mark-to-mark: management rules use entry mid, accounting uses fills
        if dte <= 0:
            out.append(ExitSignal(p, "expiry", debit))
            continue
        if pnl_per_share >= sc.profit_take * ref:
            out.append(ExitSignal(p, "profit_take", debit))
            continue
        if -pnl_per_share >= sc.loss_stop_mult * ref:
            out.append(ExitSignal(p, "stop_loss", debit))
            continue
        if dte <= sc.exit_dte:
            out.append(ExitSignal(p, "dte_exit", debit))
            continue
    return out
