"""Portfolio greek aggregation from open positions marked against a chain snapshot; beta-weighted to SPY."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from ..strategy.types import Position


@dataclass
class PortfolioGreeks:
    bw_delta: float = 0.0  # beta-weighted delta in SPY-share equivalents
    vega: float = 0.0  # $ per vol point
    gamma: float = 0.0
    theta: float = 0.0
    bp_used: float = 0.0  # sum of max loss (defined risk BP requirement)
    n_positions: int = 0
    per_underlying: dict = None

    def __post_init__(self):
        if self.per_underlying is None:
            self.per_underlying = {}


def mark_positions(
    positions: list[Position], chain: pl.DataFrame, spy_spot: float | None
) -> PortfolioGreeks:
    pg = PortfolioGreeks()
    if not positions:
        return pg
    q = chain.select(["symbol", "delta", "gamma", "theta", "vega", "spot"]).to_dict(as_series=False)
    idx = {s: i for i, s in enumerate(q["symbol"])}
    for p in positions:
        if p.status != "open":
            continue
        pg.n_positions += 1
        pg.bp_used += p.max_loss * p.qty
        pg.per_underlying[p.underlying] = pg.per_underlying.get(p.underlying, 0) + 1
        for leg in p.legs:
            i = idx.get(leg["symbol"])
            if i is None:
                continue
            mult = leg["side"] * p.qty * 100
            spot = q["spot"][i] or 0.0
            d = (q["delta"][i] or 0.0) * mult
            bw = d * p.beta * (spot / spy_spot if spy_spot else 1.0)
            pg.bw_delta += bw
            pg.vega += (q["vega"][i] or 0.0) * mult
            pg.gamma += (q["gamma"][i] or 0.0) * mult
            pg.theta += (q["theta"][i] or 0.0) * mult
    return pg
