"""F7 — Trend overlay. Directional lean from 50/200 EMA and 12-1 momentum. It does not create trades;
it (a) penalizes structures that fight the trend and (b) sets the portfolio's net beta-weighted delta target sign."""

from __future__ import annotations

import numpy as np

from ..config import Cfg
from ..strategy.types import Candidate
from .base import MarketContext


def ema(x: np.ndarray, n: int) -> np.ndarray:
    a = 2.0 / (n + 1)
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def trend_signal(closes: np.ndarray, fast: int = 50, slow: int = 200) -> tuple[int, float | None]:
    c = np.asarray(closes, dtype=float)
    if len(c) < slow + 5:
        if len(c) < fast + 5:
            return 0, None
        f = ema(c, fast)
        return (1 if c[-1] > f[-1] else -1), None
    f, s = ema(c, fast), ema(c, slow)
    mom = c[-22] / c[-252] - 1 if len(c) >= 252 else None
    up = c[-1] > s[-1] and f[-1] > s[-1]
    dn = c[-1] < s[-1] and f[-1] < s[-1]
    t = 1 if up else (-1 if dn else 0)
    if mom is not None and t != 0 and np.sign(mom) != t:
        t = 0  # disagreement → neutral
    return t, mom


class TrendFactor:
    name = "trend"

    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def score(self, ctx: MarketContext, cands: list[Candidate]) -> None:
        w = self.cfg.alpha.factor_weights.get(self.name, 1.0)
        for c in cands:
            u = ctx.underlyings.get(c.underlying)
            t = u.trend if u else 0
            if t == 0:
                c.scores[self.name] = 0.0
                continue
            # put credit is bullish (positive delta), call credit bearish
            fights = (t > 0 and c.kind == "call_credit") or (t < 0 and c.kind == "put_credit")
            aligned = (t > 0 and c.kind == "put_credit") or (t < 0 and c.kind == "call_credit")
            c.scores[self.name] = 1.0 if aligned else (-1.0 if fights else 0.0)
            if fights:
                c.confidence *= 0.4 * (1.0 / max(w, 1e-6)) if w > 1 else 0.4
            elif aligned:
                c.expected_return += w * 0.01  # modest bump; trend is a tilt, not an edge estimate
