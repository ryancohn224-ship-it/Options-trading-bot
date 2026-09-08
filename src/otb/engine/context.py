"""Builds the MarketContext for a decision date from the warehouse, point-in-time: only bars and chains with
knowable_at <= the decision instant are used. Surface history accumulates as days are processed (backtest) or
is persisted by the loader (live), so the skew percentile and HAR-IV regressor never see the future."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path

import numpy as np
import polars as pl

from ..alpha.base import MarketContext, UnderlyingContext
from ..alpha.f7_trend import trend_signal
from ..config import Cfg
from ..data.warehouse import Warehouse
from ..features.har import fit_har, latest_lags
from ..features.realized_vol import daily_rv_series, yang_zhang
from ..features.regime import regime_prob_high_vol
from ..features.surface import summarize

SURF_SCHEMA = {
    "asof": pl.Date,
    "underlying": pl.Utf8,
    "atm_iv_30": pl.Float64,
    "skew_25d": pl.Float64,
    "term_slope": pl.Float64,
    "spot": pl.Float64,
}


class SurfaceHistory:
    def __init__(self, path: Path | None):
        self.path = path
        self.df = (
            pl.read_parquet(path) if path and path.exists() else pl.DataFrame(schema=SURF_SCHEMA)
        )

    def append(self, asof: date, s) -> None:
        row = pl.DataFrame(
            {
                "asof": [asof],
                "underlying": [s.underlying],
                "atm_iv_30": [s.atm_iv_30],
                "skew_25d": [s.skew_25d],
                "term_slope": [s.term_slope],
                "spot": [s.spot],
            },
            schema=SURF_SCHEMA,
        )
        self.df = pl.concat(
            [
                self.df.filter(
                    ~((pl.col("asof") == asof) & (pl.col("underlying") == s.underlying))
                ),
                row,
            ]
        )

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.df.write_parquet(self.path)

    def series(self, underlying: str, before: date) -> pl.DataFrame:
        return self.df.filter(
            (pl.col("underlying") == underlying) & (pl.col("asof") < before)
        ).sort("asof")


class ContextBuilder:
    def __init__(
        self, cfg: Cfg, wh: Warehouse, surf: SurfaceHistory, decision_time: time = time(15, 45)
    ):
        self.cfg, self.wh, self.surf = cfg, wh, surf
        self.decision_time = decision_time
        self._regime_cache: tuple[date, float] | None = None
        self._har_cache: dict[str, tuple[date, object]] = {}

    def knowable(self, asof: date) -> datetime:
        return datetime.combine(asof, self.decision_time, tzinfo=UTC)

    def build(self, asof: date, chain: pl.DataFrame) -> MarketContext:
        kb = self.knowable(asof)
        unds: dict[str, UnderlyingContext] = {}
        market_fc = None
        p_hv = 0.0
        for sym in self.cfg.universe.symbols:
            bars = self.wh.read_bars(sym, end=asof, knowable_before=kb)
            surf = summarize(chain, sym) if not chain.is_empty() else None
            if surf is not None:
                self.surf.append(asof, surf)
            spot = surf.spot if surf else (float(bars["close"][-1]) if not bars.is_empty() else 0.0)
            rv20 = fc = None
            trend, mom = 0, None
            skew_pct = None
            if len(bars) >= 60:
                rv20 = float(yang_zhang(bars, 20)[-1])
                closes = bars["close"].to_numpy()
                trend, mom = trend_signal(
                    closes, self.cfg.alpha.trend_fast, self.cfg.alpha.trend_slow
                )
                rv = daily_rv_series(bars)
                hist = self.surf.series(sym, asof)
                iv_series = None
                if len(hist) >= 60:
                    m = bars.select(["asof"]).join(
                        hist.select(["asof", "atm_iv_30", "skew_25d"]), on="asof", how="left"
                    )
                    iv_series = m["atm_iv_30"].to_numpy().astype(float)
                    sk = m["skew_25d"].drop_nulls().to_numpy()
                    if surf is not None and len(sk) >= 30:
                        skew_pct = float(np.mean(sk[-252:] <= surf.skew_25d))
                cache = self._har_cache.get(sym)
                if cache is None or (asof - cache[0]).days >= 5:
                    model = fit_har(rv, iv=iv_series, horizon=21) or fit_har(
                        rv, iv=None, horizon=21
                    )
                    self._har_cache[sym] = (asof, model)
                else:
                    model = cache[1]
                if model is not None:
                    d, w, mm = latest_lags(rv)
                    fc = model.predict(
                        d, w, mm, iv=surf.atm_iv_30 if (surf and model.use_iv) else None
                    )
                    if rv20 and rv20 > 0:
                        fc = float(
                            min(max(fc, 0.5 * rv20), 2.0 * rv20)
                        )  # guard against extrapolation
                else:
                    fc = rv20  # HAR not yet estimable: fall back to Yang-Zhang 20d
                if sym == self.cfg.universe.symbols[0]:
                    market_fc = fc
                    if self._regime_cache is None or (asof - self._regime_cache[0]).days >= 5:
                        r = np.diff(np.log(closes))
                        p_hv = regime_prob_high_vol(r[-750:])
                        self._regime_cache = (asof, p_hv)
                    else:
                        p_hv = self._regime_cache[1]
            unds[sym] = UnderlyingContext(
                sym,
                spot,
                surf,
                fc,
                rv20,
                trend,
                mom,
                skew_pct,
                self.cfg.universe.beta.get(sym, 1.0),
            )
        return MarketContext(asof, unds, market_fc, p_hv)
