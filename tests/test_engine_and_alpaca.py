from datetime import date
from pathlib import Path

from otb.config import Cfg, Creds
from otb.data.loader import seed_synthetic
from otb.data.warehouse import Warehouse
from otb.engine.backtest import run_backtest


def test_end_to_end_backtest(tmp_path):
    cfg = Cfg()
    cfg.universe.symbols = ["SPY"]
    cfg.universe.beta = {"SPY": 1.0}
    wh = Warehouse(tmp_path / "wh")
    seed_synthetic(wh, ["SPY"], date(2023, 1, 2), 140, seed=4, s0={"SPY": 500})
    m = run_backtest(cfg, wh, 25000, out_dir=tmp_path / "bt", warmup_days=70)
    assert m["days"] == 139 and "profit_factor" in m
    assert (tmp_path / "bt" / "equity.parquet").exists()


def test_alpaca_mleg_request_builds_and_validates(monkeypatch):
    """Exercise the Alpaca broker code path against alpaca-py's own request validators with a fake client."""
    import otb.execution.alpaca_broker as ab
    from otb.data.synthetic import SyntheticMarket
    from otb.strategy.structures import build_candidates

    class FakeOrder:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class FakeTC:
        def __init__(self, *a, **k):
            self.submitted = []

        def submit_order(self, req):
            d = req.to_request_fields()
            self.submitted.append(d)
            assert (
                d["order_class"] == "mleg"
                and len(d["legs"]) in (2, 4)
                and float(d["limit_price"]) < 0
            )  # credit
            return FakeOrder(id="o1", status="filled", filled_avg_price=d["limit_price"])

        def get_order_by_id(self, oid):
            return FakeOrder(id=oid, status="filled", filled_avg_price=-0.5)

        def get_account(self):
            return FakeOrder(equity=100000, cash=100000, buying_power=200000)

        def get_all_positions(self):
            return []

    monkeypatch.setattr("alpaca.trading.client.TradingClient", FakeTC)
    cfg = Cfg()
    cfg.execution.walker_seconds_per_step = 0
    br = ab.AlpacaBroker(
        cfg, Creds(api_key="k", secret_key="s"), Path("/tmp/otb_test_positions.json")
    )
    br.positions = []
    mk = SyntheticMarket(["SPY"], date(2024, 1, 2), 2, s0={"SPY": 500})
    c = [x for x in build_candidates(mk.chain(mk.days[0]), "SPY", cfg) if x.kind == "iron_condor"][
        0
    ]
    f, pos = br.open(c, 1, mk.days[0], "h", 1.0)
    assert f.ok and pos and pos.credit_received > 0 and br.tc.submitted
    assert br.reconcile()  # ledger has a position, fake broker has none → discrepancy reported
