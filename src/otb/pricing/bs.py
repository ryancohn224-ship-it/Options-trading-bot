"""Vectorized Black-Scholes-Merton pricing, greeks, and implied vol. No external dependency.

All functions accept scalars or numpy arrays. `t` is years, `r` continuous rate, `q` dividend yield.
"""

from __future__ import annotations

import numpy as np
from scipy.special import ndtr

SQRT_2PI = np.sqrt(2.0 * np.pi)


def _pdf(x):
    return np.exp(-0.5 * x * x) / SQRT_2PI


def _d1d2(S, K, t, sigma, r, q):
    S, K, t, sigma = (np.asarray(a, dtype=float) for a in (S, K, t, sigma))
    t = np.maximum(t, 1e-9)
    sigma = np.maximum(sigma, 1e-9)
    vs = sigma * np.sqrt(t)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * t) / vs
    return d1, d1 - vs


def price(S, K, t, sigma, is_call, r=0.0, q=0.0):
    d1, d2 = _d1d2(S, K, t, sigma, r, q)
    S, K, t = (np.asarray(a, dtype=float) for a in (S, K, t))
    is_call = np.asarray(is_call, dtype=bool)
    df_r, df_q = np.exp(-r * t), np.exp(-q * t)
    call = S * df_q * ndtr(d1) - K * df_r * ndtr(d2)
    put = K * df_r * ndtr(-d2) - S * df_q * ndtr(-d1)
    return np.where(is_call, call, put)


def greeks(S, K, t, sigma, is_call, r=0.0, q=0.0):
    """Returns dict of arrays: delta, gamma, vega (per 1.00 vol = per 100 vol pts), theta (per year)."""
    d1, d2 = _d1d2(S, K, t, sigma, r, q)
    S, K, t, sigma = (np.asarray(a, dtype=float) for a in (S, K, t, sigma))
    t = np.maximum(t, 1e-9)
    is_call = np.asarray(is_call, dtype=bool)
    df_r, df_q = np.exp(-r * t), np.exp(-q * t)
    pdf1 = _pdf(d1)
    delta = np.where(is_call, df_q * ndtr(d1), -df_q * ndtr(-d1))
    gamma = df_q * pdf1 / (S * sigma * np.sqrt(t))
    vega = S * df_q * pdf1 * np.sqrt(t)
    common = -S * df_q * pdf1 * sigma / (2 * np.sqrt(t))
    theta_c = common - r * K * df_r * ndtr(d2) + q * S * df_q * ndtr(d1)
    theta_p = common + r * K * df_r * ndtr(-d2) - q * S * df_q * ndtr(-d1)
    theta = np.where(is_call, theta_c, theta_p)
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def implied_vol(mkt, S, K, t, is_call, r=0.0, q=0.0, iters=60, tol=1e-7):
    """Vectorized IV via bracketed Newton with bisection fallback. Returns NaN where no solution."""
    mkt, S, K, t = (np.asarray(a, dtype=float) for a in (mkt, S, K, t))
    is_call = np.asarray(is_call, dtype=bool)
    shape = np.broadcast(mkt, S, K, t, is_call).shape
    mkt, S, K, t, is_call = (np.broadcast_to(a, shape).copy() for a in (mkt, S, K, t, is_call))
    lo = np.full(shape, 1e-4)
    hi = np.full(shape, 5.0)
    # intrinsic check: no-arb lower bound
    df_r, df_q = np.exp(-r * t), np.exp(-q * t)
    intrinsic = np.where(
        is_call, np.maximum(S * df_q - K * df_r, 0), np.maximum(K * df_r - S * df_q, 0)
    )
    bad = (mkt <= intrinsic + 1e-10) | (t <= 0) | ~np.isfinite(mkt)
    sigma = np.full(shape, 0.3)
    for _ in range(iters):
        p = price(S, K, t, sigma, is_call, r, q)
        diff = p - mkt
        v = greeks(S, K, t, sigma, is_call, r, q)["vega"]
        # bisection update of bracket
        hi = np.where(diff > 0, sigma, hi)
        lo = np.where(diff < 0, sigma, lo)
        newton = sigma - diff / np.maximum(v, 1e-12)
        inside = (newton > lo) & (newton < hi)
        sigma = np.where(inside, newton, 0.5 * (lo + hi))
        if np.all((np.abs(diff) < tol) | bad):
            break
    sigma = np.where(bad, np.nan, sigma)
    return sigma if sigma.shape else float(sigma)
