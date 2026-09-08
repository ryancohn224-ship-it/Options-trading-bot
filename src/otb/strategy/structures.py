"""Build defined-risk candidate structures from a chain snapshot."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl

from ..config import Cfg
from .types import Candidate, Leg


def _leg(row: dict, side: int) -> Leg:
    return Leg(
        row["symbol"],
        float(row["strike"]),
        bool(row["is_call"]),
        side,
        float(row["bid"] or 0),
        float(row["ask"] or 0),
        float(row["mid"] or 0),
        float(row["delta"] or 0),
        float(row["gamma"] or 0),
        float(row["theta"] or 0),
        float(row["vega"] or 0),
        float(row["iv"] or 0),
        int(row["open_interest"] or 0),
    )


def _pick_short(g: pl.DataFrame, target_delta: float) -> dict | None:
    d = g.filter(pl.col("delta").is_not_null() & (pl.col("bid") > 0))
    if d.is_empty():
        return None
    d = d.with_columns((pl.col("delta").abs() - target_delta).abs().alias("dd")).sort("dd")
    return d.row(0, named=True)


def _pick_long(g: pl.DataFrame, strike: float) -> dict | None:
    """Nearest listed strike to the target; None if nothing is within 1% of it."""
    if g.is_empty():
        return None
    d = g.with_columns((pl.col("strike") - strike).abs().alias("dd")).sort("dd")
    row = d.row(0, named=True)
    return row if row["dd"] <= 0.01 * abs(strike) else None


def _strike_increment(strikes: np.ndarray) -> float:
    s = np.unique(np.round(strikes, 4))
    if len(s) < 2:
        return 1.0
    diffs = np.diff(s)
    return float(np.min(diffs[diffs > 0])) if np.any(diffs > 0) else 1.0


def _aggregate(
    underlying: str, kind: str, expiry: date, dte: int, legs: list[Leg], spot: float, width: float
) -> Candidate:
    credit = sum(-l.side * l.mid for l in legs)  # short legs contribute +mid
    nd = sum(l.side * l.delta for l in legs) * 100
    ng = sum(l.side * l.gamma for l in legs) * 100
    nt = sum(l.side * l.theta for l in legs) * 100
    nv = sum(l.side * l.vega for l in legs) * 100
    max_loss = (width - credit) * 100.0
    return Candidate(
        underlying, kind, expiry, dte, legs, spot, width, credit, max_loss, nd, ng, nt, nv
    )


def build_candidates(chain: pl.DataFrame, underlying: str, cfg: Cfg) -> list[Candidate]:
    sc = cfg.structure
    c = chain.filter(
        (pl.col("underlying") == underlying)
        & (pl.col("dte") >= sc.min_dte)
        & (pl.col("dte") <= sc.max_dte)
    )
    if c.is_empty():
        return []
    spot = float(c["spot"][0])
    inc = _strike_increment(c["strike"].to_numpy())
    # width ladder: 1% of spot down to 1 increment, so the optimizer can pick the widest that fits the loss budget
    top = max(inc, round(spot * sc.width_pct / inc) * inc)
    widths = sorted(
        {top, max(inc, round(top / 2 / inc) * inc), max(inc, round(top / 5 / inc) * inc), inc},
        reverse=True,
    )
    out: list[Candidate] = []
    for (expiry,), g in c.group_by(["expiry"], maintain_order=True):
        dte = int(g["dte"][0])
        for width in widths:
            puts, calls = g.filter(~pl.col("is_call")), g.filter(pl.col("is_call"))
            sp = _pick_short(puts, sc.short_delta)
            lp = _pick_long(puts, sp["strike"] - width) if sp else None
            scl = _pick_short(calls, sc.short_delta)
            lc = _pick_long(calls, scl["strike"] + width) if scl else None
            put_ok = sp and lp and lp["strike"] < sp["strike"]
            call_ok = scl and lc and lc["strike"] > scl["strike"]
            if put_ok:
                legs = [_leg(sp, -1), _leg(lp, +1)]
                w = sp["strike"] - lp["strike"]
                cand = _aggregate(underlying, "put_credit", expiry, dte, legs, spot, w)
                if cand.credit_mid / w >= sc.min_credit_to_width:
                    out.append(cand)
            if call_ok:
                legs = [_leg(scl, -1), _leg(lc, +1)]
                w = lc["strike"] - scl["strike"]
                cand = _aggregate(underlying, "call_credit", expiry, dte, legs, spot, w)
                if cand.credit_mid / w >= sc.min_credit_to_width:
                    out.append(cand)
            if put_ok and call_ok:
                legs = [_leg(sp, -1), _leg(lp, +1), _leg(scl, -1), _leg(lc, +1)]
                w = max(sp["strike"] - lp["strike"], lc["strike"] - scl["strike"])
                cand = _aggregate(underlying, "iron_condor", expiry, dte, legs, spot, w)
                if cand.credit_mid / w >= sc.min_credit_to_width:
                    out.append(cand)
    return out
