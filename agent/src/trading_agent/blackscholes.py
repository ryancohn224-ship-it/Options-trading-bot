"""Black-Scholes-Merton, only the parts this agent needs.

We carry our own rather than depending on the feed's greeks because Alpaca's
indicative options feed does not always populate them, and strike selection that
silently falls back to "whatever strike is 5 points away" is how a 16-delta condor
becomes a 30-delta condor on a high-vol morning without anyone noticing.
"""

from __future__ import annotations

import math

from .models import Right

SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def d1(spot: float, strike: float, t: float, vol: float, rate: float = 0.0) -> float:
    if t <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        raise ValueError("d1 requires positive spot, strike, time and vol")
    return (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))


def delta(spot: float, strike: float, t: float, vol: float, right: Right, rate: float = 0.0) -> float:
    """Signed delta: calls in (0, 1), puts in (-1, 0).

    At expiry (t <= 0) delta degenerates to the step function, which is the correct
    answer for a contract that is about to settle.
    """
    if t <= 0 or vol <= 0:
        if right is Right.CALL:
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    nd1 = _norm_cdf(d1(spot, strike, t, vol, rate))
    return nd1 if right is Right.CALL else nd1 - 1.0


def price(spot: float, strike: float, t: float, vol: float, right: Right, rate: float = 0.0) -> float:
    if t <= 0 or vol <= 0:
        intrinsic = spot - strike if right is Right.CALL else strike - spot
        return max(intrinsic, 0.0)
    _d1 = d1(spot, strike, t, vol, rate)
    _d2 = _d1 - vol * math.sqrt(t)
    disc = math.exp(-rate * t)
    if right is Right.CALL:
        return spot * _norm_cdf(_d1) - strike * disc * _norm_cdf(_d2)
    return strike * disc * _norm_cdf(-_d2) - spot * _norm_cdf(-_d1)


def vega(spot: float, strike: float, t: float, vol: float, rate: float = 0.0) -> float:
    if t <= 0 or vol <= 0:
        return 0.0
    return spot * _norm_pdf(d1(spot, strike, t, vol, rate)) * math.sqrt(t)


def implied_vol(
    target: float,
    spot: float,
    strike: float,
    t: float,
    right: Right,
    rate: float = 0.0,
    lo: float = 1e-4,
    hi: float = 6.0,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float | None:
    """Bisection. Returns None when the price is outside the no-arbitrage band.

    Bisection rather than Newton because 0DTE vega collapses toward zero near
    expiry and Newton's step blows up exactly when we need an answer most.
    """
    if t <= 0 or target <= 0:
        return None
    intrinsic = max((spot - strike) if right is Right.CALL else (strike - spot), 0.0)
    if target < intrinsic - tol:
        return None
    if target > price(spot, strike, t, hi, right, rate):
        return None
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        diff = price(spot, strike, t, mid, right, rate) - target
        if abs(diff) < tol:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def expected_move(spot: float, vol: float, t: float) -> float:
    """One standard deviation of the underlying over the remaining life, in dollars."""
    return spot * vol * math.sqrt(max(t, 0.0))
