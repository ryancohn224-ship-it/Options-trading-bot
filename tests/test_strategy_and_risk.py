from datetime import date

import polars as pl

from otb.config import Cfg
from otb.data.schema import EVENTS_SCHEMA
from otb.data.synthetic import SyntheticMarket
from otb.execution.sim_broker import SimBroker
from otb.portfolio.greeks import PortfolioGreeks, mark_positions
from otb.portfolio.optimizer import select
from otb.portfolio.voltarget import vol_target_scalar
from otb.risk.circuit import RiskState, can_close_today, evaluate, record_close
from otb.risk.limits import check_exits
from otb.strategy.screener import screen
from otb.strategy.structures import build_candidates


def _mk():
    mk = SyntheticMarket(["SPY"], date(2024, 1, 2), 40, s0={"SPY": 500}, seed=2)
    return mk, Cfg()


def test_candidates_defined_risk_and_widths():
    mk, cfg = _mk()
    ch = mk.chain(mk.days[0])
    cands = build_candidates(ch, "SPY", cfg)
    assert cands and all(c.max_loss > 0 and c.credit_mid > 0 for c in cands)
    assert len({c.width for c in cands}) >= 2
    ic = [c for c in cands if c.kind == "iron_condor"][0]
    assert abs(ic.net_delta) < 0.25 * 100 and ic.net_vega < 0


def test_screener_rejects_earnings_in_window():
    mk, cfg = _mk()
    ch = mk.chain(mk.days[0])
    cands = build_candidates(ch, "SPY", cfg)
    ev = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "event_type": ["earnings"],
            "event_date": [cands[0].expiry],
            "knowable_at": [None],
            "note": [""],
        },
        schema=EVENTS_SCHEMA,
    )
    _kept, why = screen(cands, cfg, ev, mk.days[0])
    assert "earnings_in_window" in why


def test_optimizer_respects_budget_and_positions():
    mk, cfg = _mk()
    ch = mk.chain(mk.days[0])
    cands = build_candidates(ch, "SPY", cfg)
    for c in cands:
        c.expected_return = 0.05
    sel, _ = select(cands, cfg, equity=25000, pg=PortfolioGreeks(), scalar=1.0, spy_spot=500)
    assert sel and all(s.cand.max_loss * s.qty <= 0.015 * 25000 + 1e-6 for s in sel)
    assert len(sel) <= cfg.risk.max_per_underlying
    sel0, _log = select(cands, cfg, equity=25000, pg=PortfolioGreeks(), scalar=0.01, spy_spot=500)
    assert not sel0


def test_vol_target_scalar_direction():
    cfg = Cfg()
    assert vol_target_scalar(cfg, 0.32) < 1.0 < vol_target_scalar(cfg, 0.08)
    assert vol_target_scalar(cfg, None) == 1.0


def test_sim_broker_cycle_and_defined_risk_cap():
    mk, cfg = _mk()
    d0 = mk.days[0]
    ch = mk.chain(d0)
    c = [x for x in build_candidates(ch, "SPY", cfg) if x.kind == "put_credit"][0]
    b = SimBroker(cfg, 10000)
    f, p = b.open(c, 1, d0, "h", 1.0)
    assert f.ok and f.price_per_share < c.credit_mid  # slippage is adverse
    assert b.account(ch).equity < 10000 + 1  # marked at mid → immediate slippage loss
    # close at a far-in-the-money world: pnl cannot exceed -max_loss
    f2 = b.close(p, mk.days[5], "test", mk.chain(mk.days[5]))
    assert f2.ok and p.realized_pnl >= -p.max_loss * p.qty - 1e-6


def test_exit_rules():
    mk, cfg = _mk()
    d0 = mk.days[0]
    ch = mk.chain(d0)
    c = [x for x in build_candidates(ch, "SPY", cfg) if x.kind == "iron_condor"][0]
    b = SimBroker(cfg, 10000)
    _, p = b.open(c, 1, d0, "h", 1.0)
    ex = check_exits([p], ch, cfg, d0)
    assert not ex  # nothing on day 0
    late = p.expiry
    assert check_exits([p], mk.chain(mk.days[-1]) if mk.days[-1] < late else ch, cfg, late)[
        0
    ].reason in ("expiry", "expiry_no_quote", "dte_exit")


def test_circuit_breakers():
    cfg = Cfg()
    st = RiskState()
    d = evaluate(st, cfg, 10000, date(2024, 1, 2))
    assert d.allow_new_entries
    d = evaluate(st, cfg, 9600, date(2024, 1, 2))
    assert not d.allow_new_entries  # -4% intraday
    d = evaluate(st, cfg, 7900, date(2024, 1, 3))
    assert d.flatten and st.killed  # -21% from HW
    st2 = RiskState()
    for _ in range(3):
        record_close(st2, -1.0, date(2024, 1, 2), date(2024, 1, 3))
    assert not evaluate(st2, cfg, 10000, date(2024, 1, 4)).allow_new_entries


def test_day_trade_guard():
    cfg = Cfg()
    st = RiskState()
    today = date(2024, 1, 5)
    for _ in range(3):
        record_close(st, 1.0, today, today)
    assert not can_close_today(st, cfg, today, today)
    assert can_close_today(st, cfg, date(2024, 1, 4), today)


def test_mark_positions_beta_weighting():
    mk, cfg = _mk()
    ch = mk.chain(mk.days[0])
    c = [x for x in build_candidates(ch, "SPY", cfg) if x.kind == "put_credit"][0]
    b = SimBroker(cfg, 10000)
    _, p = b.open(c, 2, mk.days[0], "h", 1.0)
    pg = mark_positions([p], ch, 500.0)
    assert pg.n_positions == 1 and pg.bw_delta > 0 and pg.vega < 0 and pg.bp_used == p.max_loss * 2
