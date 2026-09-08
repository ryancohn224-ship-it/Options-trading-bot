from __future__ import annotations

from datetime import date
from pathlib import Path

from ..alpha.registry import default_factors
from ..config import Cfg
from ..data.warehouse import Warehouse
from ..execution.sim_broker import SimBroker
from ..monitoring.ledger import Ledger, metrics
from ..risk.circuit import RiskState
from .context import ContextBuilder, SurfaceHistory
from .strategy import decide


def run_backtest(
    cfg: Cfg,
    wh: Warehouse,
    starting_cash: float,
    start: date | None = None,
    end: date | None = None,
    out_dir: Path | None = None,
    verbose: bool = False,
    warmup_days: int = 0,
) -> dict:
    days = [
        d for d in wh.chain_dates() if (start is None or d >= start) and (end is None or d <= end)
    ]
    if not days:
        raise SystemExit("no chain dates in warehouse for the requested range")
    broker = SimBroker(cfg, starting_cash)
    ledger = Ledger(out_dir or Path("state/backtest"))
    surf = SurfaceHistory(None)
    cb = ContextBuilder(cfg, wh, surf)
    risk = RiskState()
    factors = default_factors(cfg)
    h = cfg.hash()
    events = wh.read_events()
    reports = []
    for i, d in enumerate(days):
        chain = wh.read_chain(d, knowable_before=cb.knowable(d))
        ctx = cb.build(d, chain)
        if i < warmup_days:
            ledger.record_equity(d, broker.account(chain).equity, 0.0)
            continue
        rep = decide(cfg, ctx, chain, events, broker, risk, ledger, factors, d, h)
        reports.append(rep)
        if verbose and (
            rep.opened
            or rep.closed
            or (rep.notes and any("KILL" in n or "halt" in n for n in rep.notes))
        ):
            print(
                f"{d} eq={rep.equity:,.0f} s={rep.scalar:.2f} open={rep.opened} close={rep.closed} notes={[n for n in rep.notes if 'screened' not in n]}"
            )
    ledger.save(broker.positions)
    m = metrics(ledger.equity, broker.positions)
    m["config_hash"] = h
    m["vol_target"] = cfg.voltarget.enabled
    return m
