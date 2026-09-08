"""Execution cost, applied last: subtract the modeled round-trip slippage (open + close, each `frac` of the
half-spread per leg) from expected P&L. This is what makes narrow spreads lose to wide ones when they should."""

from __future__ import annotations

from ..config import Cfg
from ..strategy.types import Candidate
from .base import MarketContext


class CostFactor:
    name = "cost"

    def __init__(self, cfg: Cfg):
        self.frac = cfg.execution.sim_fill_frac_into_spread

    def score(self, ctx: MarketContext, cands: list[Candidate]) -> None:
        for c in cands:
            half_sum = sum(l.spread for l in c.legs) / 2
            cost = 2 * self.frac * half_sum * 100.0  # $ per contract, open + close
            c.scores[self.name] = -cost / max(c.max_loss, 1e-6)
            c.expected_return += c.scores[self.name]
