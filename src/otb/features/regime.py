"""Regime model: 2-state Gaussian HMM on (daily return, change in 30d ATM IV). Falls back to a
vol-percentile heuristic when there isn't enough history. Outputs P(high-vol regime) in [0,1]."""

from __future__ import annotations

import numpy as np


def regime_prob_high_vol(
    returns: np.ndarray, d_iv: np.ndarray | None = None, min_obs: int = 250
) -> float:
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < min_obs:
        # heuristic: recent 20d vol vs trailing 1y distribution
        if len(r) < 40:
            return 0.0
        rv20 = np.std(r[-20:])
        hist = np.array([np.std(r[i - 20 : i]) for i in range(20, len(r))])
        return float(np.mean(hist <= rv20))
    try:
        import logging

        logging.getLogger("hmmlearn").setLevel(logging.ERROR)
        from hmmlearn.hmm import GaussianHMM

        X = (
            r.reshape(-1, 1)
            if d_iv is None
            else np.column_stack([r, np.asarray(d_iv, dtype=float)[-len(r) :]])
        )
        X = X[np.all(np.isfinite(X), axis=1)]
        m = GaussianHMM(
            n_components=2,
            covariance_type="full",
            n_iter=200,
            random_state=0,
            tol=1e-2,
            verbose=False,
        )
        m.fit(X)
        post = m.predict_proba(X)[-1]
        # identify high-vol state as the one with larger return variance
        hv = int(np.argmax([m.covars_[k][0, 0] for k in range(2)]))
        return float(post[hv])
    except Exception:
        rv20 = np.std(r[-20:])
        hist = np.array([np.std(r[i - 20 : i]) for i in range(20, len(r))])
        return float(np.mean(hist <= rv20))
