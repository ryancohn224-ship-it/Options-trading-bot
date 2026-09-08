"""Synthetic market generator: correlated stochastic-vol underlyings and Black-Scholes-priced option chains
with a skewed surface, a term structure, and a *true* variance risk premium (IV = expected RV + premium).

Purpose: exercise the entire pipeline end to end without vendor data, and provide a world where a
VRP-harvesting strategy *should* work on average and vol spikes *should* hurt — so vol-targeting is testable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import polars as pl

from ..pricing import bs
from ..pricing.symbols import format_occ
from .schema import BARS_SCHEMA, CHAIN_SCHEMA, conform


def third_friday(y: int, m: int) -> date:
    d = date(y, m, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def trading_days(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _expiries(asof: date, max_days: int = 70) -> list[date]:
    exps = set()
    # weeklies (Fridays) within max_days
    d = asof + timedelta(days=1)
    while (d - asof).days <= max_days:
        if d.weekday() == 4:
            exps.add(d)
        d += timedelta(days=1)
    # monthlies
    for k in range(4):
        m = (asof.month - 1 + k) % 12 + 1
        y = asof.year + (asof.month - 1 + k) // 12
        tf = third_friday(y, m)
        if 0 < (tf - asof).days <= max_days + 30:
            exps.add(tf)
    return sorted(exps)


def _strike_inc(spot: float) -> float:
    # ETF-like granularity: $0.50 under $25, $1 up to $1000, $5 above
    return 0.5 if spot < 25 else (1.0 if spot < 1000 else 5.0)


class SyntheticMarket:
    def __init__(
        self,
        symbols: list[str],
        start: date,
        n_days: int,
        seed: int = 7,
        s0: dict[str, float] | None = None,
        vrp_pts: float = 0.03,
        corr: float = 0.7,
    ):
        self.symbols = symbols
        self.days = trading_days(start, n_days)
        self.rng = np.random.default_rng(seed)
        self.vrp_pts = vrp_pts
        n, m = len(self.days), len(symbols)
        # Heston-like variance path, shared factor + idiosyncratic
        kappa, theta_v, xi = 4.0, 0.03, 0.35
        dt = 1 / 252
        v = np.full(m, theta_v)
        S = np.array(
            [(s0 or {}).get(s, 100.0 + 50 * i) for i, s in enumerate(symbols)], dtype=float
        )
        C = np.full((m, m), corr) + np.eye(m) * (1 - corr)
        L = np.linalg.cholesky(C)
        self.close = np.zeros((n, m))
        self.open = np.zeros((n, m))
        self.high = np.zeros((n, m))
        self.low = np.zeros((n, m))
        self.var = np.zeros((n, m))
        for i in range(n):
            z = L @ self.rng.standard_normal(m)
            zv = -0.7 * z + np.sqrt(1 - 0.49) * self.rng.standard_normal(m)  # leverage effect
            v = np.maximum(v + kappa * (theta_v - v) * dt + xi * np.sqrt(v * dt) * zv, 1e-5)
            # occasional jump shock to variance (crisis)
            if self.rng.random() < 0.01:
                v = v + 0.02  # crisis shock: +~14 vol points at the base level
            v = np.minimum(v, 0.25)  # cap at 50% vol
            ret = -0.5 * v * dt + np.sqrt(v * dt) * z
            o = S * np.exp(self.rng.normal(0, np.sqrt(v * dt) * 0.3))
            c = S * np.exp(ret)
            rng_hi = np.maximum(o, c) * np.exp(np.abs(self.rng.normal(0, np.sqrt(v * dt) * 0.5)))
            rng_lo = np.minimum(o, c) * np.exp(-np.abs(self.rng.normal(0, np.sqrt(v * dt) * 0.5)))
            self.open[i], self.close[i], self.high[i], self.low[i], self.var[i] = (
                o,
                c,
                rng_hi,
                rng_lo,
                v,
            )
            S = c

    def bars(self) -> pl.DataFrame:
        rows = []
        for j, s in enumerate(self.symbols):
            for i, d in enumerate(self.days):
                rows.append(
                    (
                        d,
                        datetime.combine(d, time(21, 0), tzinfo=UTC),
                        s,
                        self.open[i, j],
                        self.high[i, j],
                        self.low[i, j],
                        self.close[i, j],
                        int(1e6),
                    )
                )
        return conform(
            pl.DataFrame(rows, schema=list(BARS_SCHEMA.keys()), orient="row"), BARS_SCHEMA
        )

    def chain(self, asof: date, snapshot_time: time = time(15, 30)) -> pl.DataFrame:
        i = self.days.index(asof)
        rows = []
        knowable = datetime.combine(asof, snapshot_time, tzinfo=UTC)
        for j, sym in enumerate(self.symbols):
            spot = self.close[i, j]
            inst_vol = np.sqrt(self.var[i, j])
            inc = _strike_inc(spot)
            strikes = np.arange(np.floor(spot * 0.80 / inc) * inc, spot * 1.20, inc)
            for exp in _expiries(asof):
                dte = (exp - asof).days
                t = dte / 365
                # term structure: mean-reverting toward long-run 0.18, plus VRP premium
                lr = 0.18
                w = np.exp(-4.0 * dte / 365)
                base = inst_vol * w + lr * (1 - w) + self.vrp_pts
                k = np.log(strikes / spot) / max(np.sqrt(t), 0.05)
                iv = base * (1 + (-0.35 * k) + 0.15 * k**2)  # put skew + smile
                iv = np.clip(iv, 0.05, 2.5)
                for is_call in (False, True):
                    px = bs.price(spot, strikes, t, iv, is_call)
                    g = bs.greeks(spot, strikes, t, iv, is_call)
                    # spread: tighter near ATM and for liquid names
                    moneyness = np.abs(np.log(strikes / spot))
                    half = np.maximum(0.005, 0.005 + 0.01 * px + 0.15 * moneyness * px)
                    bid = np.maximum(px - half, 0.0)
                    ask = px + half
                    oi = (5000 * np.exp(-8 * moneyness)).astype(int) + 50
                    for s_, b_, a_, m_, iv_, d_, ga_, th_, ve_, oi_ in zip(
                        strikes, bid, ask, px, iv, g["delta"], g["gamma"], g["theta"], g["vega"], oi
                    ):
                        rows.append(
                            (
                                asof,
                                knowable,
                                sym,
                                format_occ(sym, exp, is_call, s_),
                                exp,
                                float(s_),
                                is_call,
                                round(float(b_), 2),
                                round(float(a_), 2),
                                float(m_),
                                float(m_),
                                int(oi_ // 5),
                                int(oi_),
                                float(iv_),
                                float(d_),
                                float(ga_),
                                float(th_) / 365.0,
                                float(ve_) / 100.0,
                                float(spot),
                                int(dte),
                            )
                        )
        return conform(
            pl.DataFrame(rows, schema=list(CHAIN_SCHEMA.keys()), orient="row"), CHAIN_SCHEMA
        )

    def realized_var_true(self, asof: date, symbol: str) -> float:
        return float(self.var[self.days.index(asof), self.symbols.index(symbol)])
