from datetime import UTC, date, datetime

import polars as pl

from otb.data.synthetic import SyntheticMarket
from otb.data.warehouse import Warehouse


def test_warehouse_roundtrip_and_pit(tmp_path):
    wh = Warehouse(tmp_path)
    mk = SyntheticMarket(["SPY"], date(2024, 1, 2), 3, s0={"SPY": 500})
    wh.write_bars(mk.bars())
    for d in mk.days:
        wh.write_chain(mk.chain(d))
    assert wh.chain_dates() == mk.days
    c = wh.read_chain(mk.days[-1], "SPY")
    assert len(c) > 100 and set(c["underlying"]) == {"SPY"}
    # PIT: chain snapshot at 15:30 UTC is unknowable at 15:00 UTC
    early = datetime.combine(mk.days[-1], datetime.min.time(), tzinfo=UTC).replace(hour=15)
    assert wh.read_chain(mk.days[-1], "SPY", knowable_before=early).is_empty()
    # PIT: today's bar (knowable 21:00) is excluded at 15:45
    b = wh.read_bars("SPY", knowable_before=early.replace(minute=45))
    assert b["asof"].max() == mk.days[-2]


def test_bars_upsert(tmp_path):
    wh = Warehouse(tmp_path)
    mk = SyntheticMarket(["SPY"], date(2024, 1, 2), 5)
    b = mk.bars()
    wh.write_bars(b)
    wh.write_bars(b)
    assert len(wh.read_bars("SPY")) == 5


def test_synthetic_surface_has_put_skew_and_vrp():
    mk = SyntheticMarket(["SPY"], date(2024, 1, 2), 2, s0={"SPY": 500}, vrp_pts=0.03)
    c = mk.chain(mk.days[-1]).filter((pl.col("dte") > 25) & (pl.col("dte") < 50))
    p = c.filter(~pl.col("is_call") & (pl.col("delta").abs().is_between(0.2, 0.3)))["iv"].mean()
    k = c.filter(pl.col("is_call") & (pl.col("delta").abs().is_between(0.2, 0.3)))["iv"].mean()
    assert p > k
