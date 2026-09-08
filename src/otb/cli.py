from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import typer
from rich import print as rprint

from .config import Creds, load_cfg
from .data.warehouse import Warehouse

app = typer.Typer(help="otb — options trading bot", no_args_is_help=True)


@app.command()
def synth(
    days: int = 500,
    symbols: str = "SPY,QQQ",
    start: str = "2022-01-03",
    seed: int = 7,
    warehouse: str = "warehouse",
    vrp: float = 0.03,
):
    """Seed the warehouse with a synthetic market (for pipeline tests and offline backtests)."""
    from .data.loader import seed_synthetic

    wh = Warehouse(warehouse)
    syms = [s.strip() for s in symbols.split(",")]
    s0 = {
        s: p for s, p in zip(syms, [500, 420, 200, 180, 400, 800, 180, 170, 500, 40, 90, 200, 95])
    }
    r = seed_synthetic(wh, syms, date.fromisoformat(start), days, seed=seed, s0=s0, vrp_pts=vrp)
    rprint(r)


@app.command()
def backtest(
    config: str = "config/default.yaml",
    warehouse: str = "warehouse",
    cash: float = 25000,
    start: str | None = None,
    end: str | None = None,
    out: str = "state/backtest",
    verbose: bool = False,
    warmup: int = 60,
    no_voltarget: bool = False,
):
    """Replay the warehouse through the strategy with the simulated broker."""
    from .engine.backtest import run_backtest

    cfg = load_cfg(config)
    if no_voltarget:
        cfg.voltarget.enabled = False
    m = run_backtest(
        cfg,
        Warehouse(warehouse),
        cash,
        date.fromisoformat(start) if start else None,
        date.fromisoformat(end) if end else None,
        Path(out),
        verbose,
        warmup,
    )
    rprint(json.dumps(m, indent=2, default=str))


@app.command()
def load(
    config: str = "config/default.yaml", warehouse: str = "warehouse", asof: str | None = None
):
    """Pull today's bars + option chain snapshots from Alpaca into the warehouse."""
    from .data.loader import load_alpaca_snapshot

    cfg = load_cfg(config)
    creds = Creds.from_env()
    if not creds.present:
        raise SystemExit("set APCA_API_KEY_ID / APCA_API_SECRET_KEY")
    rprint(
        load_alpaca_snapshot(
            cfg, creds, Warehouse(warehouse), date.fromisoformat(asof) if asof else None
        )
    )


@app.command()
def trade(
    config: str = "config/default.yaml",
    warehouse: str = "warehouse",
    dry_run: bool = False,
    no_load: bool = False,
    force: bool = False,
    asof: str | None = None,
):
    """Run one paper/live decision cycle against Alpaca (dry-run: compute decisions, submit nothing)."""
    from .engine.live import run_live

    cfg = load_cfg(config)
    creds = Creds.from_env()
    r = run_live(
        cfg,
        creds,
        Warehouse(warehouse),
        date.fromisoformat(asof) if asof else None,
        load=not no_load,
        dry_run=dry_run,
        force_closed_market=force,
    )
    rprint(json.dumps(r, indent=2, default=str))


@app.command()
def status(config: str = "config/default.yaml"):
    """Account, open positions, risk state, and reconciliation."""
    from .execution.alpaca_broker import AlpacaBroker
    from .risk.circuit import RiskState

    cfg = load_cfg(config)
    creds = Creds.from_env()
    if not creds.present:
        raise SystemExit("set APCA_API_KEY_ID / APCA_API_SECRET_KEY")
    b = AlpacaBroker(cfg, creds, Path(cfg.data.state_dir) / "positions.json")
    a = b.account()
    rprint(
        {
            "equity": a.equity,
            "cash": a.cash,
            "buying_power": a.buying_power,
            "clock_open": b.clock().is_open,
        }
    )
    for p in b.open_positions():
        rprint(
            f"  {p.id} {p.underlying} {p.kind} exp={p.expiry} qty={p.qty} credit={p.credit_received:.2f} opened={p.open_date}"
        )
    rprint(
        {
            "risk": RiskState.load(Path(cfg.data.state_dir) / "risk.json").__dict__,
            "reconcile": b.reconcile(),
        }
    )


@app.command()
def doctor(config: str = "config/default.yaml"):
    """First-run checks: credentials, connectivity, account, options approval, data feed."""
    cfg = load_cfg(config)
    creds = Creds.from_env()
    rprint({"config": config, "config_hash": cfg.hash(), "mode": cfg.mode, "feed": cfg.data.feed})
    if not creds.present:
        rprint("[red]✗ APCA_API_KEY_ID / APCA_API_SECRET_KEY not set[/red]")
        raise typer.Exit(1)
    rprint(f"✓ credentials present (paper={creds.paper})")
    try:
        from alpaca.trading.client import TradingClient

        tc = TradingClient(creds.api_key, creds.secret_key, paper=creds.paper)
        a = tc.get_account()
        c = tc.get_clock()
        rprint(
            f"✓ trading API: equity={float(a.equity):,.2f} buying_power={float(a.buying_power):,.2f} status={a.status} options_level={getattr(a, 'options_approved_level', '?')} trading_level={getattr(a, 'options_trading_level', '?')}"
        )
        rprint(f"  clock: open={c.is_open} next_open={c.next_open} next_close={c.next_close}")
        if int(getattr(a, "options_approved_level", 0) or 0) < 3:
            rprint(
                "[yellow]! options approval level < 3: multi-leg spreads will be rejected. Apply for Level 3.[/yellow]"
            )
    except Exception as e:
        rprint(f"[red]✗ trading API: {e}[/red]")
        raise typer.Exit(1)
    try:
        from .data.alpaca_data import AlpacaData

        ad = AlpacaData(creds, feed=cfg.data.feed)
        s = ad.spot(cfg.universe.symbols[0])
        rprint(f"✓ stock data: {cfg.universe.symbols[0]} last trade {s}")
        ch = ad.chain(
            cfg.universe.symbols[0], min_dte=cfg.structure.min_dte, max_dte=cfg.structure.max_dte
        )
        rprint(
            f"✓ options chain ({cfg.data.feed}): {len(ch)} contracts, {ch['iv'].is_not_null().sum()} with IV, {ch['open_interest'].sum()} total OI"
        )
    except Exception as e:
        rprint(f"[red]✗ market data: {e}[/red]")
        raise typer.Exit(1)
    rprint("[green]all checks passed — next: otb load, then otb trade --dry-run[/green]")


@app.command()
def metrics(out: str = "state/backtest"):
    """Recompute metrics from a saved ledger."""
    import polars as pl

    from .monitoring.ledger import metrics as _m
    from .strategy.types import Position

    e = pl.read_parquet(Path(out) / "equity.parquet")
    t = (
        pl.read_parquet(Path(out) / "trades.parquet")
        if (Path(out) / "trades.parquet").exists()
        else None
    )
    pos = []
    if t is not None:
        for r in t.to_dicts():
            r["legs"], r["meta"] = [], {}
            pos.append(Position.from_dict(r))
    rprint(
        json.dumps(
            _m([(r["date"], r["equity"], r["vol_scalar"]) for r in e.to_dicts()], pos),
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    app()
