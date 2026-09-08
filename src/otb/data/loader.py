"""Nightly loader: pulls bars + chain snapshots into the warehouse. Also seeds from the synthetic market."""

from __future__ import annotations

from datetime import date, timedelta

from ..config import Cfg, Creds
from .warehouse import Warehouse


def load_alpaca_snapshot(cfg: Cfg, creds: Creds, wh: Warehouse, asof: date | None = None) -> dict:
    from .alpaca_data import AlpacaData

    ad = AlpacaData(creds, feed=cfg.data.feed)
    asof = asof or date.today()
    start = asof - timedelta(days=cfg.data.stock_history_days)
    n_bars = wh.write_bars(ad.daily_bars(cfg.universe.symbols, start, asof))
    n_chain = 0
    for sym in cfg.universe.symbols:
        n_chain += wh.write_chain(
            ad.chain(sym, asof, min_dte=1, max_dte=cfg.structure.max_dte + 20)
        )
    return {"bars": n_bars, "chain_rows": n_chain, "asof": asof}


def seed_synthetic(
    wh: Warehouse, symbols: list[str], start: date, n_days: int, seed: int = 7, **kw
) -> dict:
    from .synthetic import SyntheticMarket

    mk = SyntheticMarket(symbols, start, n_days, seed=seed, **kw)
    wh.write_bars(mk.bars())
    n = 0
    for d in mk.days:
        n += wh.write_chain(mk.chain(d))
    return {"days": len(mk.days), "chain_rows": n}
