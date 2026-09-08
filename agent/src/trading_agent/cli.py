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
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .broker import SimBroker
from .clock import Clock
from .config import AgentConfig, load_config
from .journal import Journal, render_markdown
from .marketdata import SyntheticChainSource
from .research import ResearchLog, check_promotion
from .session import Session
from .snapshot import SnapshotStore
from .state import Store
from .variants import champion as champion_of
from .variants import load_variants, save_variants


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
        snapshots=SnapshotStore(config.paths.snapshot_dir),
        fidelity="live" if args.source == "alpaca" else "synthetic",
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




# -- research ----------------------------------------------------------------------


def _cmd_simulate(config: AgentConfig, args) -> int:
    """Generate synthetic sessions so the research loop has something to chew on.

    Everything written is stamped `synthetic` and can never promote a variant. This
    exercises the machinery; it says nothing about the market.
    """
    from .simulate import simulate

    store = SnapshotStore(config.paths.snapshot_dir)
    written = simulate(store, config, days=args.days, seed=args.seed)
    print(f"wrote {len(written)} synthetic sessions to {config.paths.snapshot_dir}")
    print(f"  {written[0]} .. {written[-1]}")
    print("\nSynthetic days prove the pipeline runs. They cannot promote anything.")
    return 0


def _cmd_replay(config: AgentConfig, args) -> int:
    from .report import evaluate_all

    store = SnapshotStore(config.paths.snapshot_dir)
    if not store.days():
        print(f"no snapshots in {config.paths.snapshot_dir} — run `simulate` or trade a session")
        return 1
    variants = load_variants(config.paths.variants_file)
    if args.variant:
        variants = [v for v in variants if v.name == args.variant] or variants
    runs = evaluate_all(store, config, variants, equity=args.equity)

    print(f"{'variant':22} {'trades':>6} {'rate':>5} {'win%':>5} {'meanR':>7} "
          f"{'total':>9} {'holdout R':>10}  verdict")
    from .report import _rank

    for run in _rank(runs):
        s, h = run.research, run.holdout
        print(f"{run.variant.name:22} {s.trades:6d} {s.trade_rate:5.0%} {s.win_rate:5.0%} "
              f"{s.mean_r:+7.3f} {s.total_pnl:+9.0f} "
              f"{(f'{h.mean_r:+.3f}' if args.show_holdout else '  hidden'):>10}  {s.verdict}")
    if not args.show_holdout:
        print("\nHoldout column hidden. Opening it is a decision — use `promote`, which logs it.")
    return 0


def _cmd_report(config: AgentConfig, args) -> int:
    from . import report as report_mod

    day = date.fromisoformat(args.day) if args.day else datetime.now(
        ZoneInfo(config.market.timezone)
    ).date()
    text = report_mod.render(
        store=SnapshotStore(config.paths.snapshot_dir),
        config=config,
        variants=load_variants(config.paths.variants_file),
        log=ResearchLog(config.paths.research_dir),
        journal_dir=config.paths.journal_dir,
        day=day,
        equity=args.equity,
    )
    out = Path(config.paths.report_dir) / f"{day.isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)
    print(f"\n[written to {out}]")
    return 0


def _cmd_propose(config: AgentConfig, args) -> int:
    """Pre-register a hypothesis before looking at whether it worked."""
    log = ResearchLog(config.paths.research_dir)
    log.propose(args.variant, args.hypothesis, args.criterion)
    print(f"registered proposal for `{args.variant}`")
    print("  hypothesis:", args.hypothesis)
    print("  criterion :", args.criterion)
    return 0


def _cmd_promote(config: AgentConfig, args) -> int:
    """Open the holdout for one challenger and decide. Every call is logged."""
    from .report import evaluate_all

    store = SnapshotStore(config.paths.snapshot_dir)
    variants = load_variants(config.paths.variants_file)
    names = {v.name for v in variants}
    if args.variant not in names:
        print(f"unknown variant {args.variant!r}; known: {', '.join(sorted(names))}")
        return 1

    runs = {r.variant.name: r for r in evaluate_all(store, config, variants, equity=args.equity)}
    incumbent = champion_of(variants)
    log = ResearchLog(config.paths.research_dir)
    log.record_holdout_look(args.variant, args.reason or "promotion check")

    verdict = check_promotion(
        challenger=args.variant,
        research_challenger=runs[args.variant].research,
        research_champion=runs[incumbent.name].research,
        holdout_challenger=runs[args.variant].holdout,
        holdout_looks=log.holdout_looks(),
    )
    print(verdict)

    if verdict.approved and args.apply:
        rebuilt = [
            type(v)(v.name, v.overrides, v.note,
                    "champion" if v.name == args.variant
                    else ("challenger" if v.status == "champion" else v.status),
                    v.retired_reason)
            for v in variants
        ]
        save_variants(rebuilt, config.paths.variants_file)
        promoted = next(v for v in rebuilt if v.name == args.variant)
        log.record_promotion(verdict, incumbent.name, promoted.name)
        print(f"\n`{args.variant}` is now champion. Copy its overrides into "
              f"{config.paths.variants_file}'s champion entry and agent.yaml to trade it.")
    elif verdict.approved:
        print("\nApproved but not applied. Re-run with --apply to make it champion.")
    else:
        log.record_promotion(verdict, incumbent.name, incumbent.name)
    return 0


def _cmd_variants(config: AgentConfig, args) -> int:
    for v in load_variants(config.paths.variants_file):
        mark = {"champion": "👑", "challenger": "  ", "retired": "💀"}[v.status]
        print(f"{mark} {v.name:22} {v.overrides}")
        if v.note:
            print(f"     {v.note}")
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

    sim = sub.add_parser("simulate", help="generate synthetic sessions")
    sim.add_argument("--days", type=int, default=120)
    sim.add_argument("--seed", type=int, default=4)
    sim.set_defaults(func=_cmd_simulate)

    rep = sub.add_parser("replay", help="score every variant over stored snapshots")
    rep.add_argument("--variant", default=None)
    rep.add_argument("--show-holdout", action="store_true",
                     help="reveal holdout results (prefer `promote`, which logs the look)")
    rep.set_defaults(func=_cmd_replay)

    rpt = sub.add_parser("report", help="write the end-of-day report")
    rpt.add_argument("--day", default=None)
    rpt.set_defaults(func=_cmd_report)

    prop = sub.add_parser("propose", help="pre-register a hypothesis")
    prop.add_argument("--variant", required=True)
    prop.add_argument("--hypothesis", required=True)
    prop.add_argument("--criterion", required=True)
    prop.set_defaults(func=_cmd_propose)

    prom = sub.add_parser("promote", help="open the holdout and decide on a challenger")
    prom.add_argument("--variant", required=True)
    prom.add_argument("--reason", default=None)
    prom.add_argument("--apply", action="store_true")
    prom.set_defaults(func=_cmd_promote)

    sub.add_parser("variants").set_defaults(func=_cmd_variants)

    args = parser.parse_args(argv)
    config = load_config(args.config)
    return args.func(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
