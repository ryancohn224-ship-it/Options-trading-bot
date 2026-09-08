"""IV surface summaries from a chain snapshot: ATM IV by expiry, 30-day interpolated ATM IV,
25-delta skew, term slope."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass
class SurfaceSummary:
    underlying: str
    spot: float
    atm_iv_30: float  # interpolated 30-day ATM IV
    skew_25d: float  # IV(25d put) - IV(25d call), at ~30d
    term_slope: float  # (ATM IV 60d - ATM IV 30d) / 30d ; >0 contango
    atm_by_expiry: dict  # dte -> atm iv


def _atm_iv(df: pl.DataFrame, spot: float) -> float | None:
    d = df.filter(pl.col("iv").is_not_null() & (pl.col("iv") > 0)).with_columns(
        (pl.col("strike") - spot).abs().alias("dist")
    )
    if d.is_empty():
        return None
    near = d.sort("dist").head(4)
    return float(near["iv"].mean())


def _iv_at_delta(df: pl.DataFrame, target: float) -> float | None:
    d = df.filter(pl.col("iv").is_not_null() & pl.col("delta").is_not_null())
    if d.is_empty():
        return None
    d = d.with_columns((pl.col("delta").abs() - abs(target)).abs().alias("dd")).sort("dd").head(2)
    return float(d["iv"].mean())


def summarize(chain: pl.DataFrame, underlying: str) -> SurfaceSummary | None:
    c = chain.filter(pl.col("underlying") == underlying)
    if c.is_empty():
        return None
    spot = float(c["spot"][0])
    by = {}
    for (dte,), g in c.group_by(["dte"], maintain_order=True):
        if dte < 5:
            continue
        v = _atm_iv(g, spot)
        if v:
            by[int(dte)] = v
    if len(by) < 2:
        return None
    dtes = np.array(sorted(by))
    ivs = np.array([by[d] for d in dtes])

    # variance-time interpolation
    def interp(target):
        if target <= dtes[0]:
            return float(ivs[0])
        if target >= dtes[-1]:
            return float(ivs[-1])
        tv = ivs**2 * dtes
        return float(np.sqrt(np.interp(target, dtes, tv) / target))

    iv30, iv60 = interp(30), interp(60)
    # skew at the expiry nearest 30d
    nearest = int(dtes[np.argmin(np.abs(dtes - 30))])
    g = c.filter(pl.col("dte") == nearest)
    p25 = _iv_at_delta(g.filter(~pl.col("is_call")), -0.25)
    c25 = _iv_at_delta(g.filter(pl.col("is_call")), 0.25)
    skew = (p25 - c25) if (p25 and c25) else 0.0
    return SurfaceSummary(underlying, spot, iv30, skew, (iv60 - iv30) / 30.0, by)
