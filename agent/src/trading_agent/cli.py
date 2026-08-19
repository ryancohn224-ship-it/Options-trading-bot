"""Command line entry points — one short-lived process per cron slot.

    trading-agent preflight   # what would stop me trading right now
    trading-agent open        # attempt one entry
    trading-agent manage      # one management pass
    trading-agent demo        # a full synthetic session, no keys, no market
    trading-agent review      # what the journal says about the last N sessions

`open` and `manage` are separate processes on purpose: a long-lived loop that dies
mid-session loses its position; a cron slot that dies just misses one pass, and the
next one reads the position back off disk.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .broker import SimBroker
from .clock import Clock
from .config import AgentConfig, load_config
from .journal import Journal, render_markdown
from .marketdata import SyntheticChainSource
from .session import Session
from .state import Store


def _build(config: AgentConfig, args) -> Session:
    env = dict(os.environ)
    clock = Clock(config)

    if args.source == "alpaca" or args.broker == "alpaca":
        key, secret = env.get("ALPACA_API_KEY"), env.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            sys.exit("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set for live data or trading")

    if args.source == "alpaca":
        from .marketdata import AlpacaChainSource

        source = AlpacaChainSource(key, secret, feed=env.get("ALPACA_OPTIONS_FEED", "opra"))
    else:
        source = SyntheticChainSource(spot=args.spot, iv=args.iv)

    if args.broker == "alpaca":
        from .broker import AlpacaBroker

        broker = AlpacaBroker(key, secret, paper=config.mode == "paper")
    else:
        broker = SimBroker(equity=args.equity)

    return Session(
        config=config,
        clock=clock,
        source=source,
        broker=broker,
        store=Store(config.paths.state_dir),
        journal=Journal(config.paths.journal_dir),
        env=env,
    )


def _cmd_preflight(config: AgentConfig, args) -> int:
    from .guardrails import preflight

    session = _build(config, args)
    state = session.store.load_state()
    state.roll_to(session.clock.today())
    try:
        account = session.broker.get_account()
    except Exception as exc:  # noqa: BLE001
        print(f"account unavailable: {exc}")
        account = None
    result = preflight(
        config,
        session.clock,
        state,
        account,
        session.env,
        holding_position=session.store.load_position() is not None,
    )
    for row in result.report.results:
        print(row)
    print("\nclear to trade" if result.ok else f"\nstanding down: {result.report.reason}")
    return 0 if result.ok else 1


def _cmd_open(config: AgentConfig, args) -> int:
    result = _build(config, args).open_position()
    print(f"{result.outcome}: {result.detail}")
    return 0 if result.outcome in {"traded", "no_trade"} else 2


def _cmd_manage(config: AgentConfig, args) -> int:
    result = _build(config, args).manage()
    print(f"{result.outcome}: {result.detail}")
    return 0


def _cmd_demo(config: AgentConfig, args) -> int:
    """A full session against a synthetic chain: entry, drift, exit, journal.

    Runs anywhere, any time of day, with no credentials. This is the path to exercise
    after changing anything, and the fastest way to see what the gates actually do.
    """
    tz = ZoneInfo(config.market.timezone)
    day = datetime.now(tz).date()
    entry_at = datetime.combine(day, config.schedule.entry_start, tzinfo=tz) + timedelta(minutes=15)
    expiry_at = datetime.combine(day, config.market.expiry_time, tzinfo=tz)

    store = Store(config.paths.state_dir)
    journal = Journal(config.paths.journal_dir)
    broker = SimBroker(equity=args.equity)

    def make(now, spot, iv):
        return Session(
            config=config,
            clock=Clock(config, now=now),
            source=SyntheticChainSource(spot=spot, iv=iv, as_of=now, expiry_at=expiry_at,
                                        prev_close=args.spot),
            broker=broker,
            store=store,
            journal=journal,
            env=dict(os.environ),
            sleep=lambda _s: None,
        )

    print(f"--- {entry_at:%H:%M} entry attempt, {args.underlying_label} {args.spot:.2f} @ {args.iv:.1%} IV")
    result = make(entry_at, args.spot, args.iv).open_position()
    print(f"    {result.outcome}: {result.detail}")
    if result.outcome != "traded":
        print(f"\njournal: {config.paths.journal_dir}/{result.entry.session_date}.md")
        return 0

    # The last pass is deliberately past force_flat: a demo that never exercises the
    # flatten-no-matter-what path is not demonstrating the part that matters most.
    for minutes, drift in ((60, 0.0), (150, 0.0015), (300, 0.0), (340, 0.0)):
        at = entry_at + timedelta(minutes=minutes)
        spot = args.spot * (1 + drift)
        session = make(at, spot, args.iv)
        broker.close_cost = None
        chain = session.source.get_chain(config.market.underlying, day)
        position = store.load_position()
        if position is None:
            break
        from .session import close_cost

        broker.close_cost = close_cost(chain, position)
        outcome = session.manage()
        print(f"--- {at:%H:%M} manage, spot {spot:.2f} → {outcome.outcome}: {outcome.detail}")
        if outcome.outcome == "traded":
            break

    print(f"\njournal: {config.paths.journal_dir}/{day.isoformat()}.md")
    return 0


def _cmd_review(config: AgentConfig, args) -> int:
    """The weekly read. Skipped cycles are the headline, not a footnote."""
    path = Path(config.paths.journal_dir) / "trades.jsonl"
    if not path.exists():
        print(f"no journal at {path}")
        return 1

    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    # A trading day writes several rows (one entry attempt, one exit, possibly an
    # escalation). Count *days*, so the skip rate means what it looks like it means.
    days = sorted({r["session_date"] for r in rows})[-args.limit :]
    rows = [r for r in rows if r["session_date"] in set(days)]

    traded = [r for r in rows if r.get("realized_pnl") is not None]
    opened = {r["session_date"] for r in rows if r["outcome"] == "traded" and not r.get("exit_reason")}
    stood_down = [r for r in rows if r["outcome"] == "no_trade"]
    halted = [r for r in rows if r["outcome"] == "halted"]
    errors = [r for r in rows if r["outcome"] == "error"]
    wins = [r for r in traded if r["realized_pnl"] > 0]
    pnl = sum(r["realized_pnl"] for r in traded)

    print(f"days logged          {len(days)}")
    print(f"  days it traded     {len(opened)} ({len(opened) / len(days):.0%})")
    print(f"  days it stood down {len(stood_down)}")
    print(f"  days it halted     {len(halted)}")
    if errors:
        print(f"  ESCALATIONS        {len(errors)}  <- read these first")
    print(f"  closed trades      {len(traded)}")
    if traded:
        gross_win = sum(r["realized_pnl"] for r in wins)
        gross_loss = -sum(r["realized_pnl"] for r in traded if r["realized_pnl"] <= 0)
        print(f"win rate             {len(wins)}/{len(traded)} ({len(wins) / len(traded):.0%})")
        print(f"net P&L              {pnl:+,.2f}")
        print(f"profit factor        {gross_win / gross_loss:.2f}" if gross_loss else
              "profit factor        n/a (no losses yet — too few trades to mean anything)")

    reasons: dict[str, int] = {}
    for row in stood_down:
        for gate in row.get("gates", []) + row.get("preflight", []):
            if not gate["passed"]:
                reasons[gate["name"]] = reasons.get(gate["name"], 0) + 1
    if reasons:
        print("\nwhy it stood down:")
        for name, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {name:<20} {count}")
    return 0


def _cmd_show(config: AgentConfig, args) -> int:
    path = Path(config.paths.journal_dir) / "trades.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    from .journal import JournalEntry

    print(render_markdown(JournalEntry(**rows[-1])))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trading-agent", description=__doc__)
    parser.add_argument("--config", default=None, help="path to agent.yaml")
    parser.add_argument("--broker", choices=("sim", "alpaca"), default="sim")
    parser.add_argument("--source", choices=("synthetic", "alpaca"), default="synthetic")
    parser.add_argument("--equity", type=float, default=25_000.0, help="sim broker equity")
    parser.add_argument("--spot", type=float, default=600.0, help="synthetic spot")
    parser.add_argument("--iv", type=float, default=0.13, help="synthetic ATM implied vol")
    parser.add_argument("--underlying-label", default="SPY", help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (
        ("preflight", _cmd_preflight),
        ("open", _cmd_open),
        ("manage", _cmd_manage),
        ("demo", _cmd_demo),
        ("show", _cmd_show),
    ):
        sub.add_parser(name).set_defaults(func=fn)
    review = sub.add_parser("review")
    review.add_argument("--limit", type=int, default=60)
    review.set_defaults(func=_cmd_review)

    args = parser.parse_args(argv)
    config = load_config(args.config)
    return args.func(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
