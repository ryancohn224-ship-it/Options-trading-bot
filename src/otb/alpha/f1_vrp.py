"""F1 — Variance risk premium. Edge = IV30 − HAR-IV forecast (vol points).
Expected P&L for a short-vega structure ≈ −net_vega × VRP × capture, where capture reflects that a
30-45 DTE position managed at 50% profit realizes roughly half the premium decay over its hold."""

from __future__ import annotations

from ..config import Cfg
from ..strategy.types import Candidate
from .base import MarketContext


class VRPFactor:
    name = "vrp"

    def __init__(self, cfg: Cfg, capture: float = 0.5):
        self.cfg = cfg
        self.capture = capture

    def score(self, ctx: MarketContext, cands: list[Candidate]) -> None:
        w = self.cfg.alpha.factor_weights.get(self.name, 1.0)
        for c in cands:
            u = ctx.underlyings.get(c.underlying)
            if not u or not u.surface or u.rv_forecast is None:
                c.scores[self.name] = 0.0
                c.confidence *= 0.5
                continue
            vrp = u.surface.atm_iv_30 - u.rv_forecast
            c.scores[self.name] = vrp
            if vrp < self.cfg.alpha.vrp_min_edge_vol_pts:
                c.confidence *= (
                    0.2  # premium not rich enough: strongly discourage short-vol entries
                )
            # expected $ from premium harvesting over the hold, per contract; vega is $ per vol pt per contract
            exp_pnl = -c.net_vega * (vrp * 100.0) * self.capture
            c.expected_return += w * exp_pnl / max(c.max_loss, 1e-6)
