from datetime import date

import numpy as np

from otb.pricing import bs
from otb.pricing.symbols import format_occ, parse_occ


def test_put_call_parity():
    S, K, t, sig = 100.0, 95.0, 0.5, 0.25
    c = bs.price(S, K, t, sig, True)
    p = bs.price(S, K, t, sig, False)
    assert abs((c - p) - (S - K)) < 1e-9


def test_iv_roundtrip_vectorized():
    K = np.array([80, 90, 100, 110, 120.0])
    sig = np.array([0.35, 0.28, 0.22, 0.2, 0.24])
    px = bs.price(100, K, 0.3, sig, np.array([False, False, True, True, True]))
    iv = bs.implied_vol(px, 100, K, 0.3, np.array([False, False, True, True, True]))
    assert np.allclose(iv, sig, atol=1e-5)


def test_iv_nan_below_intrinsic():
    assert np.isnan(bs.implied_vol(0.5, 100, 90, 0.5, True))


def test_greeks_sign_and_delta_bounds():
    g = bs.greeks(100, np.array([90, 100, 110.0]), 0.25, 0.2, np.array([True, True, True]))
    assert (
        np.all((g["delta"] > 0) & (g["delta"] < 1))
        and np.all(g["gamma"] > 0)
        and np.all(g["vega"] > 0)
        and np.all(g["theta"] < 0)
    )


def test_occ_symbols():
    s = format_occ("SPY", date(2026, 3, 20), False, 450.5)
    assert s == "SPY260320P00450500"
    k = parse_occ(s)
    assert k.strike == 450.5 and not k.is_call and k.expiry == date(2026, 3, 20) and k.symbol == s
