"""HAR-RV(-IV) forecaster (Corsi 2009; Kambouroudis et al. 2021).

  RV_{t+1..t+h} = b0 + b_d RV_d + b_w RV_w + b_m RV_m [+ b_iv IV_t] + e

Fit by OLS on log-variance (stabilizes the residuals). Forecast returns *annualized vol* for horizon h days.
Point-in-time: fit only on rows whose targets are fully realized before the decision date.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class HARModel:
    coef: np.ndarray
    use_iv: bool
    horizon: int
    n_obs: int
    r2: float

    def predict(self, rv_d: float, rv_w: float, rv_m: float, iv: float | None = None) -> float:
        x = [1.0, np.log(rv_d + 1e-10), np.log(rv_w + 1e-10), np.log(rv_m + 1e-10)]
        if self.use_iv:
            x.append(np.log((iv if iv is not None and iv > 0 else np.sqrt(rv_m)) ** 2 + 1e-10))
        logvar = float(np.dot(self.coef, x))
        return float(np.sqrt(np.exp(logvar)))


def _lags(rv: np.ndarray):
    n = len(rv)
    d = rv.copy()
    w = np.full(n, np.nan)
    m = np.full(n, np.nan)
    for i in range(n):
        if i >= 4:
            w[i] = np.mean(rv[i - 4 : i + 1])
        if i >= 21:
            m[i] = np.mean(rv[i - 21 : i + 1])
    return d, w, m


def fit_har(
    rv_daily_var: np.ndarray, iv: np.ndarray | None = None, horizon: int = 21, min_obs: int = 60
) -> HARModel | None:
    """rv_daily_var: annualized daily realized variance series (one value per day).
    iv: matching implied vol (annualized, as vol) series or None. Returns None if insufficient data."""
    rv = np.asarray(rv_daily_var, dtype=float)
    n = len(rv)
    d, w, m = _lags(rv)
    # target: mean realized variance over next `horizon` days
    y = np.full(n, np.nan)
    for i in range(n - horizon):
        y[i] = np.mean(rv[i + 1 : i + 1 + horizon])
    cols = [np.ones(n), np.log(d + 1e-10), np.log(w + 1e-10), np.log(m + 1e-10)]
    use_iv = iv is not None
    if use_iv:
        ivv = np.asarray(iv, dtype=float) ** 2
        cols.append(np.log(np.where(ivv > 0, ivv, np.nan) + 1e-10))
    X = np.column_stack(cols)
    ylog = np.log(y + 1e-10)
    ok = np.all(np.isfinite(X), axis=1) & np.isfinite(ylog)
    if ok.sum() < min_obs:
        return None
    Xo, yo = X[ok], ylog[ok]
    coef, *_ = np.linalg.lstsq(Xo, yo, rcond=None)
    resid = yo - Xo @ coef
    r2 = 1 - resid.var() / max(yo.var(), 1e-12)
    return HARModel(coef=coef, use_iv=use_iv, horizon=horizon, n_obs=int(ok.sum()), r2=float(r2))


def latest_lags(rv_daily_var: np.ndarray) -> tuple[float, float, float]:
    d, w, m = _lags(np.asarray(rv_daily_var, dtype=float))
    return float(d[-1]), float(w[-1]), float(m[-1])
