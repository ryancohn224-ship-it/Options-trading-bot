"""The daily decision. Identical for backtest and live: the only difference is which Broker and which chain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import polars as pl

from ..alpha.base import MarketContext
from ..config import Cfg
from ..execution.broker import Broker
from ..monitoring.ledger import Ledger
from ..portfolio.greeks import mark_positions
from ..portfolio.optimizer import select
from ..portfolio.voltarget import vol_target_scalar
from ..risk.circuit import RiskState, can_close_today, evaluate, record_close
from ..risk.limits import check_exits
from ..strategy.screener import screen
from ..strategy.structures import build_candidates


@dataclass
class DayReport:
    asof: date
    equity: float
    scalar: float
    opened: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def decide(
    cfg: Cfg,
    ctx: MarketContext,
    chain: pl.DataFrame,
    events: pl.DataFrame,
    broker: Broker,
    risk_state: RiskState,
    ledger: Ledger,
    factors,
    today: date,
    config_hash: str,
) -> DayReport:
    acct = broker.account(chain)
    rep = DayReport(today, acct.equity, 1.0)
    decision = evaluate(risk_state, cfg, acct.equity, today)
    rep.notes += decision.reasons

    # 1) exits
    for ex in check_exits(broker.open_positions(), chain, cfg, today):
        p = ex.position
        if not can_close_today(risk_state, cfg, p.open_date, today) and ex.reason == "profit_take":
            rep.notes.append(f"day-trade guard: deferring close of {p.id}")
            continue
        f = broker.close(p, today, ex.reason, chain)
        if f.ok:
            record_close(risk_state, p.realized_pnl or 0.0, p.open_date, today)
            ledger.record_slip(
                today, p.id, "close", f.mid_at_submit, f.price_per_share, p.qty, len(p.legs)
            )
            rep.closed.append(f"{p.underlying} {p.kind} {ex.reason} pnl={p.realized_pnl:.0f}")
        else:
            rep.notes.append(f"close failed {p.id}: {f.detail}")

    # 2) kill switch → flatten
    if decision.flatten:
        for p in broker.open_positions():
            f = broker.close(p, today, "kill_switch", chain)
            if f.ok:
                record_close(risk_state, p.realized_pnl or 0.0, p.open_date, today)
                rep.closed.append(f"{p.underlying} {p.kind} kill pnl={p.realized_pnl:.0f}")
        acct = broker.account(chain)
        rep.equity = acct.equity
        ledger.record_equity(today, acct.equity, 0.0)
        return rep

    # 3) entries
    scalar = vol_target_scalar(cfg, ctx.market_vol_forecast, ctx.p_high_vol) * decision.size_mult
    rep.scalar = scalar
    if decision.allow_new_entries and not chain.is_empty():
        cands = []
        for sym in cfg.universe.symbols:
            cands += build_candidates(chain, sym, cfg)
        kept, rejected = screen(cands, cfg, events, today)
        for f in factors:
            f.score(ctx, kept)
        spy = ctx.underlyings.get(cfg.universe.symbols[0])
        spy_spot = spy.spot if spy else None
        pg = mark_positions(broker.open_positions(), chain, spy_spot)
        trend_sign = spy.trend if spy else 0
        sels, _log = select(
            kept,
            cfg,
            acct.equity,
            pg,
            scalar,
            spy_spot,
            delta_target_sign=trend_sign,
            min_expected_return=cfg.alpha.min_expected_return,
        )
        for s in sels:
            beta = cfg.universe.beta.get(s.cand.underlying, 1.0)
            f, pos = broker.open(s.cand, s.qty, today, config_hash, beta, chain)
            if f.ok and pos:
                ledger.record_slip(
                    today,
                    pos.id,
                    "open",
                    f.mid_at_submit,
                    f.price_per_share,
                    s.qty,
                    len(s.cand.legs),
                )
                rep.opened.append(f"{s.cand.describe()} x{s.qty} fill={f.price_per_share:.2f}")
            else:
                rep.notes.append(f"open failed {s.cand.underlying} {s.cand.kind}: {f.detail}")
        if rejected:
            rep.notes.append(f"screened out: {rejected}")
    acct = broker.account(chain)
    rep.equity = acct.equity
    ledger.record_equity(today, acct.equity, scalar)
    return rep
