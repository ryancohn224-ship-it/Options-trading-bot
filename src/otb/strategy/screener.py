"""Hard gates: liquidity per leg, underlying price, and event blackout (earnings inside the trade window)."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from ..config import Cfg
from .types import Candidate


def liquidity_ok(c: Candidate, cfg: Cfg) -> tuple[bool, str]:
    u = cfg.universe
    if c.spot < u.min_underlying_price:
        return False, "price"
    for l in c.legs:
        if l.bid <= 0 or l.ask <= 0:
            return False, "no_quote"
        if l.open_interest < u.min_open_interest:
            return False, f"oi<{u.min_open_interest}"
        if l.spread > u.max_spread_abs and l.spread > u.max_spread_pct_of_mid * max(l.mid, 1e-6):
            return False, "wide_spread"
    return True, "ok"


def event_ok(
    c: Candidate, events: pl.DataFrame, asof: date, blackout_days: int = 0
) -> tuple[bool, str]:
    """Reject if an earnings event for the underlying falls between now and expiry (+blackout)."""
    if events.is_empty():
        return True, "ok"
    e = events.filter(
        (pl.col("symbol") == c.underlying)
        & (pl.col("event_type") == "earnings")
        & (pl.col("event_date") >= asof)
        & (pl.col("event_date") <= c.expiry + timedelta(days=blackout_days))
    )
    return (e.is_empty(), "ok" if e.is_empty() else "earnings_in_window")


def screen(
    cands: list[Candidate], cfg: Cfg, events: pl.DataFrame, asof: date
) -> tuple[list[Candidate], dict]:
    keep, reasons = [], {}
    for c in cands:
        ok, why = liquidity_ok(c, cfg)
        if ok:
            ok, why = event_ok(c, events, asof)
        if ok:
            keep.append(c)
        else:
            reasons[why] = reasons.get(why, 0) + 1
    return keep, reasons
