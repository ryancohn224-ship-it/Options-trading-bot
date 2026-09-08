"""Statistics, the holdout, and the rules for changing anything.

The failure mode this module exists to prevent: an agent that looks at yesterday's
loss, tightens a threshold, sees the backtest improve, and repeats — arriving in three
months at a configuration beautifully fitted to noise, with a track record that proves
it. Nothing about that process feels like overfitting from the inside. It feels like
diligence.

Four defences, in order of how much they matter:

1. **A holdout the optimiser never sees.** Days are assigned by a stable hash of the
   date, so the split is fixed in advance and does not drift as data arrives. Research
   happens on research days; a change is confirmed on holdout days or not at all.
2. **Minimum sample before anything moves.** A variant with eleven trades has no
   information in it, however good the eleven look.
3. **A correction for having looked K times.** Test ten variants and the best one is
   flattered by chance alone. `best_of_k_pvalue` measures exactly how flattered.
4. **A record of every look.** Each time the holdout is opened it loses a little of its
   power. Counting the looks is what stops that from happening silently.

None of this makes a bad strategy good. It makes an honest answer reachable, including
the answer "there is nothing here" — which `verdict` is willing to return.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from .replay import ReplayResult
from .snapshot import PROMOTABLE_FIDELITY

#: Share of days locked away from the optimiser.
HOLDOUT_PCT = 25

#: Below this many trades, any statistic is decoration.
MIN_TRADES_FOR_A_VERDICT = 30


# --------------------------------------------------------------------------------
# The split
# --------------------------------------------------------------------------------


def is_holdout(day: date, pct: int = HOLDOUT_PCT) -> bool:
    """Stable, date-derived, decided before the data existed.

    A random split would reshuffle every run; a "last N days" split would let the
    optimiser watch the holdout fill up. Hashing the date fixes each day's role
    permanently, so a variant written today can be tested against holdout days from
    months ago without anyone having chosen which days those are.
    """
    digest = hashlib.sha256(day.isoformat().encode()).digest()
    return (int.from_bytes(digest[:4], "big") % 100) < pct


def split(days: Iterable[date], pct: int = HOLDOUT_PCT) -> tuple[list[date], list[date]]:
    research, holdout = [], []
    for day in days:
        (holdout if is_holdout(day, pct) else research).append(day)
    return research, holdout


# --------------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------------


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — behaves sensibly at small n, unlike the normal
    approximation, which happily reports a 100% win rate with no uncertainty."""
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def bootstrap_ci(
    values: Sequence[float], alpha: float = 0.05, iterations: int = 4000, seed: int = 11
) -> tuple[float, float]:
    """Percentile bootstrap on the mean.

    Trade P&L is bounded below by max loss and above by the credit — sharply skewed and
    nothing like normal — so a t-interval would misstate the tails in the direction
    that matters.
    """
    if len(values) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations)
    )
    lo = means[int(alpha / 2 * iterations)]
    hi = means[min(int((1 - alpha / 2) * iterations), iterations - 1)]
    return (lo, hi)


def max_drawdown(pnls: Sequence[float]) -> float:
    """Deepest peak-to-trough decline of the cumulative curve, as a positive number."""
    peak = running = 0.0
    worst = 0.0
    for pnl in pnls:
        running += pnl
        peak = max(peak, running)
        worst = max(worst, peak - running)
    return worst


def trades_needed(values: Sequence[float], power: float = 0.80) -> int | None:
    """How many trades it would take to call the observed edge real.

    The number most of these projects never compute, and the one that explains why a
    promising twenty-trade result is not evidence of anything.
    """
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    sd = statistics.stdev(values)
    if mean == 0 or sd == 0:
        return None
    z_alpha, z_beta = 1.96, 0.84 if power == 0.80 else 1.28
    return max(1, math.ceil(((z_alpha + z_beta) ** 2) * (sd**2) / (mean**2)))


def best_of_k_pvalue(
    best_mean: float, values: Sequence[float], k: int, iterations: int = 2000, seed: int = 17
) -> float:
    """How often K coin-flipping variants produce a winner this good.

    Testing ten variants and reporting the best is ten chances to be lucky. This
    resamples the observed distribution around a zero mean, takes the best of K each
    time, and returns the share of draws that beat what was actually seen. A large
    value means the leaderboard's top row is a selection effect.
    """
    if len(values) < 2 or k < 1:
        return float("nan")
    rng = random.Random(seed)
    n = len(values)
    centred = [v - statistics.fmean(values) for v in values]
    beats = 0
    for _ in range(iterations):
        best = max(
            sum(centred[rng.randrange(n)] for _ in range(n)) / n for _ in range(k)
        )
        if best >= best_mean:
            beats += 1
    return beats / iterations


@dataclass(frozen=True)
class Summary:
    variant: str
    days: int
    trades: int
    wins: int
    total_pnl: float
    mean_pnl: float
    mean_r: float
    win_rate: float
    win_rate_ci: tuple[float, float]
    pnl_ci: tuple[float, float]
    profit_factor: float | None
    max_drawdown: float
    trades_for_significance: int | None
    promotable_trades: int

    @property
    def trade_rate(self) -> float:
        return self.trades / self.days if self.days else 0.0

    @property
    def verdict(self) -> str:
        if self.trades < MIN_TRADES_FOR_A_VERDICT:
            return "insufficient"
        lo, hi = self.pnl_ci
        if math.isnan(lo):
            return "insufficient"
        if lo > 0:
            return "positive"
        if hi < 0:
            return "negative"
        return "indistinguishable from zero"


def summarize(results: Sequence[ReplayResult], variant: str, days: int) -> Summary:
    traded = [r for r in results if r.entered]
    pnls = [r.pnl for r in traded]
    rs = [r.r_multiple for r in traded]
    wins = sum(1 for p in pnls if p > 0)
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p <= 0)
    promotable = sum(1 for r in traded if r.fidelity in PROMOTABLE_FIDELITY)

    return Summary(
        variant=variant,
        days=days,
        trades=len(traded),
        wins=wins,
        total_pnl=sum(pnls),
        mean_pnl=statistics.fmean(pnls) if pnls else 0.0,
        mean_r=statistics.fmean(rs) if rs else 0.0,
        win_rate=wins / len(pnls) if pnls else 0.0,
        win_rate_ci=wilson_interval(wins, len(pnls)),
        pnl_ci=bootstrap_ci(pnls),
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else None,
        max_drawdown=max_drawdown(pnls),
        trades_for_significance=trades_needed(pnls),
        promotable_trades=promotable,
    )


# --------------------------------------------------------------------------------
# Promotion
# --------------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionRules:
    min_research_trades: int = 40
    min_holdout_trades: int = 12
    #: Challenger must beat the champion by this much per trade, in R, on research days.
    min_edge_r: float = 0.02
    require_positive_holdout: bool = True
    #: Past this many openings the holdout has been optimised against and must be recut.
    max_holdout_looks: int = 10


@dataclass(frozen=True)
class PromotionVerdict:
    challenger: str
    approved: bool
    reasons: tuple[str, ...]

    def __str__(self) -> str:
        head = "APPROVED" if self.approved else "REJECTED"
        return f"{head}: {self.challenger}\n" + "\n".join(f"  - {r}" for r in self.reasons)


def check_promotion(
    challenger: str,
    research_challenger: Summary,
    research_champion: Summary,
    holdout_challenger: Summary,
    holdout_looks: int,
    rules: PromotionRules = PromotionRules(),
) -> PromotionVerdict:
    """Every criterion is evaluated and reported, not short-circuited at the first no.

    A rejection that names one reason invites fixing that one thing and asking again,
    which is itself a form of overfitting — to the promotion rule.
    """
    reasons: list[str] = []
    ok = True

    if research_challenger.promotable_trades < rules.min_research_trades:
        ok = False
        reasons.append(
            f"{research_challenger.promotable_trades} live-fidelity research trades, "
            f"needs {rules.min_research_trades} (synthetic and backfilled days do not count)"
        )
    else:
        reasons.append(f"{research_challenger.promotable_trades} live research trades ✓")

    edge = research_challenger.mean_r - research_champion.mean_r
    if edge < rules.min_edge_r:
        ok = False
        reasons.append(
            f"edge over champion {edge:+.3f}R per trade, needs {rules.min_edge_r:+.3f}R"
        )
    else:
        reasons.append(f"edge over champion {edge:+.3f}R per trade ✓")

    if holdout_challenger.promotable_trades < rules.min_holdout_trades:
        ok = False
        reasons.append(
            f"{holdout_challenger.promotable_trades} live holdout trades, "
            f"needs {rules.min_holdout_trades}"
        )
    elif rules.require_positive_holdout and holdout_challenger.mean_r <= 0:
        ok = False
        reasons.append(
            f"holdout expectancy {holdout_challenger.mean_r:+.3f}R — the research result "
            "did not survive data the search never saw"
        )
    else:
        reasons.append(f"holdout expectancy {holdout_challenger.mean_r:+.3f}R ✓")

    if holdout_looks >= rules.max_holdout_looks:
        ok = False
        reasons.append(
            f"holdout has been opened {holdout_looks} times and is no longer independent; "
            "recut it against fresh days before promoting anything else"
        )

    return PromotionVerdict(challenger, ok, tuple(reasons))


# --------------------------------------------------------------------------------
# The record
# --------------------------------------------------------------------------------


@dataclass
class ResearchLog:
    """Append-only logs for proposals, promotions and holdout openings.

    Pre-registration is the point. A hypothesis written down before the holdout is
    opened can be wrong; one written afterwards is unfalsifiable, because the result is
    already known and the reasoning arrives to fit it.
    """

    root: Path

    def _append(self, name: str, row: dict[str, Any]) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"logged_at": datetime.now().astimezone().isoformat(timespec="seconds")} | row
        with path.open("a") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def _read(self, name: str) -> list[dict[str, Any]]:
        path = self.root / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def propose(self, variant: str, hypothesis: str, success_criterion: str) -> None:
        self._append(
            "proposals.jsonl",
            {"variant": variant, "hypothesis": hypothesis, "criterion": success_criterion},
        )

    def proposals(self) -> list[dict[str, Any]]:
        return self._read("proposals.jsonl")

    def record_holdout_look(self, variant: str, reason: str) -> None:
        self._append("holdout_looks.jsonl", {"variant": variant, "reason": reason})

    def holdout_looks(self) -> int:
        return len(self._read("holdout_looks.jsonl"))

    def record_promotion(self, verdict: PromotionVerdict, from_config: str, to_config: str) -> None:
        self._append(
            "promotions.jsonl",
            {
                "challenger": verdict.challenger, "approved": verdict.approved,
                "reasons": list(verdict.reasons),
                "config_before": from_config, "config_after": to_config,
            },
        )

    def promotions(self) -> list[dict[str, Any]]:
        return self._read("promotions.jsonl")

    def record_retirement(self, variant: str, reason: str, summary: Summary) -> None:
        self._append(
            "retirements.jsonl",
            {"variant": variant, "reason": reason, "summary": asdict(summary)},
        )
