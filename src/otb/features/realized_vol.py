"""Realized volatility estimators. All return *annualized* vol (not variance) as numpy arrays aligned to input rows,
with NaN for the warm-up window."""

from __future__ import annotations

import numpy as np
import polars as pl

ANN = 252.0


def _roll_mean(x: np.ndarray, w: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= w:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[w - 1 :] = (c[w:] - c[:-w]) / w
    return out


def close_to_close(bars: pl.DataFrame, window: int = 20) -> np.ndarray:
    c = bars["close"].to_numpy()
    r = np.diff(np.log(c), prepend=np.nan)
    r2 = np.where(np.isnan(r), 0.0, r**2)
    v = _roll_mean(r2, window)
    v[:window] = np.nan
    return np.sqrt(v * ANN)


def parkinson(bars: pl.DataFrame, window: int = 20) -> np.ndarray:
    h, l = bars["high"].to_numpy(), bars["low"].to_numpy()
    x = np.log(h / l) ** 2 / (4 * np.log(2))
    return np.sqrt(_roll_mean(x, window) * ANN)


def yang_zhang(bars: pl.DataFrame, window: int = 20) -> np.ndarray:
    """Yang-Zhang (2000): handles overnight gaps and drift; the most efficient OHLC estimator."""
    o, h, l, c = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))
    n = len(c)
    if n < window + 1:
        return np.full(n, np.nan)
    co = np.log(o[1:] / c[:-1])  # overnight
    oc = np.log(c[1:] / o[1:])  # open-to-close
    rs = np.log(h[1:] / o[1:]) * np.log(h[1:] / c[1:]) + np.log(l[1:] / o[1:]) * np.log(
        l[1:] / c[1:]
    )
    k = 0.34 / (1.34 + (window + 1) / (window - 1))
    out = np.full(n, np.nan)
    for i in range(window, n):
        s = slice(i - window, i)
        vo = np.var(co[s], ddof=1)
        vc = np.var(oc[s], ddof=1)
        vrs = np.mean(rs[s])
        out[i] = np.sqrt(max(vo + k * vc + (1 - k) * vrs, 0.0) * ANN)
    return out


def daily_rv_series(bars: pl.DataFrame) -> np.ndarray:
    """One-day realized variance proxy per row (Parkinson daily), annualized variance. Used by HAR."""
    h, l = bars["high"].to_numpy(), bars["low"].to_numpy()
    return (np.log(h / l) ** 2 / (4 * np.log(2))) * ANN
