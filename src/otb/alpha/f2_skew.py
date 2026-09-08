"""F2 — Skew premium. When the 25-delta put/call skew is rich vs its own history, favor selling the put side
(put credit spread) over the call side; when cheap, the reverse. Contribution scaled by skew excess."""

from __future__ import annotations

from ..config import Cfg
from ..strategy.types import Candidate
from .base import MarketContext


class SkewFactor:
    name = "skew"

    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def score(self, ctx: MarketContext, cands: list[Candidate]) -> None:
        w = self.cfg.alpha.factor_weights.get(self.name, 0.5)
        rich = self.cfg.alpha.skew_rich_percentile
        for c in cands:
            u = ctx.underlyings.get(c.underlying)
            if not u or u.skew_pct is None or not u.surface:
                c.scores[self.name] = 0.0
                continue
            # signed tilt in [-1, 1]: +1 = puts rich
            tilt = (u.skew_pct - 0.5) * 2.0
            if c.kind == "put_credit":
                s = tilt
            elif c.kind == "call_credit":
                s = -tilt
            else:
                s = 0.0
            c.scores[self.name] = s * abs(u.surface.skew_25d)
            # translate to expected return: a rich skew adds roughly skew_25d vol pts of premium on the put side
            exp_pnl = -c.net_vega * (c.scores[self.name] * 100.0) * 0.5
            c.expected_return += w * exp_pnl / max(c.max_loss, 1e-6)
            if u.skew_pct >= rich and c.kind == "call_credit":
                c.confidence *= 0.8
