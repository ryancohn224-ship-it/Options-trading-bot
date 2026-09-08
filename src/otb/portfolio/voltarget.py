"""Moreira–Muir volatility targeting: scale the risk budget by target_vol / forecast_vol, clipped."""

from __future__ import annotations

from ..config import Cfg


def vol_target_scalar(
    cfg: Cfg, market_vol_forecast: float | None, p_high_vol: float = 0.0
) -> float:
    vt = cfg.voltarget
    if not vt.enabled or not market_vol_forecast or market_vol_forecast <= 0:
        return 1.0
    s = vt.reference_market_vol / market_vol_forecast
    # regime overlay: in a high-vol state, cut further (short-vol losses cluster there)
    s *= 1.0 - 0.4 * p_high_vol
    return float(min(max(s, vt.min_scalar), vt.max_scalar))
