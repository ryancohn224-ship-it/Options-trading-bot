from datetime import date

import numpy as np

from otb.alpha.f7_trend import trend_signal
from otb.data.synthetic import SyntheticMarket
from otb.features.har import fit_har, latest_lags
from otb.features.realized_vol import daily_rv_series, yang_zhang
from otb.features.surface import summarize


def test_har_recovers_vrp_in_synthetic_world():
    mk = SyntheticMarket(["SPY"], date(2020, 1, 6), 700, seed=3, vrp_pts=0.03)
    b = mk.bars()
    rv = daily_rv_series(b)
    w = np.exp(-4 * 30 / 365)
    iv = np.sqrt(mk.var[:, 0]) * w + 0.18 * (1 - w) + 0.03
    m = fit_har(rv, iv=iv, horizon=21)
    assert m is not None and m.r2 > 0.3
    d, wk, mo = latest_lags(rv)
    fc = m.predict(d, wk, mo, iv=iv[-1])
    assert 0.01 < fc < 0.6 and (iv[-1] - fc) > 0.0  # implied above forecast → positive VRP


def test_yang_zhang_reasonable():
    mk = SyntheticMarket(["SPY"], date(2020, 1, 6), 200, seed=5)
    yz = yang_zhang(mk.bars(), 20)
    assert np.isnan(yz[:20]).all() and 0.03 < np.nanmedian(yz) < 0.8


def test_surface_summary():
    mk = SyntheticMarket(["SPY"], date(2024, 1, 2), 2, s0={"SPY": 500})
    s = summarize(mk.chain(mk.days[-1]), "SPY")
    assert s and 0.05 < s.atm_iv_30 < 1.0 and len(s.atm_by_expiry) >= 3


def test_trend_signal():
    up = np.linspace(100, 200, 300)
    dn = up[::-1]
    assert trend_signal(up)[0] == 1 and trend_signal(dn)[0] == -1
