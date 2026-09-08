"""One live/paper decision run. Intended to be invoked once per trading day (cron) after the loader.
Steps: reconcile ledger vs broker → load today's chain → build context → decide() → persist state."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from ..alpha.registry import default_factors
from ..config import Cfg, Creds
from ..data.loader import load_alpaca_snapshot
from ..data.warehouse import Warehouse
from ..execution.alpaca_broker import AlpacaBroker
from ..monitoring.ledger import Ledger
from ..risk.circuit import RiskState
from .context import ContextBuilder, SurfaceHistory
from .strategy import decide


def run_live(
    cfg: Cfg,
    creds: Creds,
    wh: Warehouse,
    asof: date | None = None,
    load: bool = True,
    dry_run: bool = False,
    force_closed_market: bool = False,
) -> dict:
    if not creds.present:
        raise SystemExit("Alpaca credentials missing: set APCA_API_KEY_ID and APCA_API_SECRET_KEY")
    state_dir = Path(cfg.data.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    broker = AlpacaBroker(cfg, creds, state_dir / "positions.json", dry_run=dry_run)
    clock = broker.clock()
    if not clock.is_open and not force_closed_market and not dry_run:
        return {"skipped": "market closed", "next_open": str(clock.next_open)}
    asof = asof or datetime.now(UTC).date()
    if load:
        load_alpaca_snapshot(cfg, creds, wh, asof)
    issues = broker.reconcile()
    if issues and not dry_run:
        return {"halted": "reconciliation mismatch", "issues": issues}
    surf = SurfaceHistory(state_dir / "surface_history.parquet")
    cb = ContextBuilder(cfg, wh, surf)
    chain = wh.read_chain(
        asof, knowable_before=cb.knowable(asof) if False else None
    )  # live: use everything loaded today
    ctx = cb.build(asof, chain)
    risk = RiskState.load(state_dir / "risk.json")
    ledger = Ledger(state_dir / "ledger")
    rep = decide(
        cfg,
        ctx,
        chain,
        wh.read_events(),
        broker,
        risk,
        ledger,
        default_factors(cfg),
        asof,
        cfg.hash(),
    )
    risk.save(state_dir / "risk.json")
    surf.save()
    ledger.save(broker.positions)
    return {
        "asof": str(asof),
        "equity": rep.equity,
        "vol_scalar": rep.scalar,
        "opened": rep.opened,
        "closed": rep.closed,
        "notes": rep.notes,
        "dry_run": dry_run,
        "reconcile_issues": issues,
        "market_vol_forecast": ctx.market_vol_forecast,
        "p_high_vol": ctx.p_high_vol,
    }
