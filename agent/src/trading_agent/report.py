"""The end-of-day report.

Written to be read in two minutes by someone deciding whether to keep going. It leads
with the verdict, because the verdict is usually "not enough data yet" and everything
below it is then context rather than news.

Three rules it follows, all of them about not flattering the strategy:

* **Fidelity is always visible.** Synthetic and backfilled days are labelled everywhere
  they appear and are excluded from anything that could change the live configuration.
* **Sample size comes before performance.** A win rate with eleven trades behind it is
  reported alongside the number of trades it would take to mean something.
* **The leaderboard is corrected for being a leaderboard.** Ranking ten variants and
  reporting the winner is ten chances to be lucky; `best_of_k_pvalue` says how lucky.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import AgentConfig
from .replay import ReplayResult
from .research import (
    HOLDOUT_PCT,
    MIN_TRADES_FOR_A_VERDICT,
    ResearchLog,
    Summary,
    best_of_k_pvalue,
    split,
    summarize,
)
from .snapshot import PROMOTABLE_FIDELITY, SnapshotStore
from .variants import Variant


@dataclass
class VariantRun:
    variant: Variant
    research: Summary
    holdout: Summary
    results: list[ReplayResult]


def evaluate_all(
    store: SnapshotStore,
    config: AgentConfig,
    variants: list[Variant],
    days: list[date] | None = None,
    equity: float = 25_000.0,
) -> list[VariantRun]:
    """Replay every variant over every stored day, split into research and holdout."""
    from .replay import replay_day

    loaded = dict(store.iter_days(days))
    if not loaded:
        return []
    research_days, holdout_days = split(sorted(loaded))

    runs: list[VariantRun] = []
    for variant in variants:
        resolved = variant.apply_to(config)
        results = [replay_day(loaded[d], resolved, variant.name, equity) for d in sorted(loaded)]
        by_day = {r.day: r for r in results}
        runs.append(
            VariantRun(
                variant=variant,
                research=summarize(
                    [by_day[d] for d in research_days if d in by_day], variant.name,
                    len(research_days),
                ),
                holdout=summarize(
                    [by_day[d] for d in holdout_days if d in by_day], variant.name,
                    len(holdout_days),
                ),
                results=results,
            )
        )
    return runs


def _fidelity_mix(store: SnapshotStore, days: list[date]) -> dict[str, int]:
    mix: dict[str, int] = {}
    for day in days:
        tier = store.fidelity_of(day) or "unknown"
        mix[tier] = mix.get(tier, 0) + 1
    return mix


def _today_section(journal_dir: Path, day: date) -> str:
    """Lift today's outcome straight from the journal rather than recomputing it."""
    path = Path(journal_dir) / "trades.jsonl"
    if not path.exists():
        return "_No journal yet._"
    rows = [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip() and json.loads(line).get("session_date") == day.isoformat()
    ]
    if not rows:
        return f"_No session recorded for {day.isoformat()}._"

    lines = []
    for row in rows:
        when = row.get("started_at", "")[11:19]
        if row.get("exit_reason"):
            lines.append(
                f"- `{when}` closed on **{row['exit_reason']}** — "
                f"P&L **{row.get('realized_pnl', 0):+.2f}**"
            )
        elif row["outcome"] == "traded":
            lines.append(
                f"- `{when}` opened {row.get('contracts')} contract(s) for "
                f"{row.get('credit_per_contract', 0):.2f} credit"
            )
        else:
            failures = [
                g["name"] for g in (row.get("gates") or []) + (row.get("preflight") or [])
                if not g["passed"]
            ]
            reason = ", ".join(failures) or row.get("selection") or "—"
            lines.append(f"- `{when}` **{row['outcome']}** — {reason}")
    return "\n".join(lines)


def _rank(runs: list[VariantRun]) -> list[VariantRun]:
    """Best first — but a variant that never traded is not winning, it is absent.

    A gate strict enough to stand down every day scores a flawless +0.000R, and
    without this it sorts above everything that actually took risk.
    """
    return sorted(runs, key=lambda r: (r.research.trades == 0, -r.research.mean_r))


def _leaderboard(runs: list[VariantRun]) -> str:
    ranked = _rank(runs)
    lines = [
        "| variant | trades | rate | win% | mean R | total P&L | max DD | verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for run in ranked:
        s = run.research
        flag = " 👑" if run.variant.status == "champion" else ""
        lines.append(
            f"| `{run.variant.name}`{flag} | {s.trades} | {s.trade_rate:.0%} | "
            f"{s.win_rate:.0%} | {s.mean_r:+.3f} | {s.total_pnl:+,.0f} | "
            f"{s.max_drawdown:,.0f} | {s.verdict} |"
        )
    return "\n".join(lines)


def _verdict_section(runs: list[VariantRun], promotable_days: int) -> str:
    if not runs:
        return "**No data yet.** Nothing has been stored to replay."

    ranked = _rank(runs)
    best = ranked[0]
    if best.research.trades == 0:
        return "**No variant has taken a trade yet.** Nothing to compare."
    pnls = [r.pnl for r in best.results if r.entered]
    p = best_of_k_pvalue(best.research.mean_pnl, pnls, len(runs)) if len(pnls) > 2 else float("nan")

    lines: list[str] = []

    if promotable_days == 0:
        lines.append(
            "> **Nothing here can change the live configuration.** Every stored day is "
            "synthetic or backfilled. These runs prove the machinery works; they are not "
            "evidence about the market."
        )
    elif best.research.promotable_trades < MIN_TRADES_FOR_A_VERDICT:
        lines.append(
            f"> **Too early to conclude anything.** {best.research.promotable_trades} "
            f"live-fidelity trades on the best variant; a verdict needs at least "
            f"{MIN_TRADES_FOR_A_VERDICT}, and a promotion needs 40."
        )

    s = best.research
    lines.append("")
    lines.append(f"Best on research days: **`{best.variant.name}`** at {s.mean_r:+.3f}R per trade.")
    lo, hi = s.pnl_ci
    if lo == lo:  # not NaN
        lines.append(
            f"95% interval on mean P&L per trade: **{lo:+.2f} to {hi:+.2f}** — "
            + (
                "excludes zero."
                if lo > 0 or hi < 0
                else "**includes zero**, so the observed edge is not distinguishable from luck."
            )
        )
    if s.trades_for_significance:
        lines.append(
            f"At the observed mean and spread, calling this real would take about "
            f"**{s.trades_for_significance:,} trades** ({s.trades_for_significance / 250:.1f} "
            f"years at one a day). It has {s.trades}."
        )
    if p == p:
        lines.append(
            f"Best-of-{len(runs)} check: {p:.0%} of the time, {len(runs)} variants with no "
            "edge at all produce a winner this good. "
            + ("**That is most of the time — this leaderboard's top row is a selection effect.**"
               if p > 0.20 else "Low enough to be interesting, not low enough to act on alone.")
        )
    return "\n".join(lines)


def render(
    store: SnapshotStore,
    config: AgentConfig,
    variants: list[Variant],
    log: ResearchLog,
    journal_dir: Path,
    day: date,
    equity: float = 25_000.0,
) -> str:
    days = store.days()
    runs = evaluate_all(store, config, variants, days, equity)
    mix = _fidelity_mix(store, days)
    promotable_days = sum(n for tier, n in mix.items() if tier in PROMOTABLE_FIDELITY)
    research_days, holdout_days = split(days)

    out = [
        f"# Trading day report — {day.isoformat()}",
        "",
        f"`{config.mode}` · config `{config.hash}` · {len(days)} stored session(s) "
        f"({', '.join(f'{n} {tier}' for tier, n in sorted(mix.items())) or 'none'})",
        "",
        "## Today",
        "",
        _today_section(journal_dir, day),
        "",
        "## Verdict",
        "",
        _verdict_section(runs, promotable_days),
        "",
        "## Variant leaderboard",
        "",
        f"Research days only ({len(research_days)} of {len(days)}; "
        f"{len(holdout_days)} held out and not shown here).",
        "",
        _leaderboard(runs) if runs else "_Nothing stored to replay yet._",
        "",
    ]

    proposals = log.proposals()
    if proposals:
        out += ["## Open proposals", ""]
        for row in proposals[-5:]:
            out.append(
                f"- **`{row['variant']}`** — {row['hypothesis']}  \n"
                f"  _Success criterion:_ {row['criterion']}"
            )
        out.append("")

    promotions = log.promotions()
    looks = log.holdout_looks()
    out += [
        "## Process",
        "",
        f"- Holdout: {HOLDOUT_PCT}% of days, assigned by a stable hash of the date.",
        f"- Holdout openings so far: **{looks}** (the holdout stops being independent "
        "after about 10).",
        f"- Promotions to date: **{len([p for p in promotions if p['approved']])}** "
        f"of {len(promotions)} attempts.",
        "",
    ]
    return "\n".join(out)
