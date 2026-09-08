"""When to get out — shared verbatim by the live agent and the replay engine.

This module exists because of a bug worth remembering. The exit rules originally
compared a *marketable* cost-to-close against a threshold set as a multiple of the
credit received. Both numbers had crossed the bid/ask, in opposite directions, so on a
four-leg structure the cost-to-close was a full round-trip spread above the credit
before the market had moved at all. On a $1-wide SPY condor that spread is comparable
to the entire credit, which put the stop *inside* it: every trade stopped out seconds
after it opened, and the backtest reported a 0% win rate rather than an error.

The fix is a distinction that has to be maintained everywhere:

* **Decisions use the mid mark.** Mid is an unbiased estimate of what the structure is
  worth. It is the only fair thing to compare a threshold against.
* **Orders and P&L use marketable prices.** We really do pay the spread to get in and
  out, and pretending otherwise is how a backtest invents profit.

The stop is expressed as a fraction of *max loss*, not as a multiple of the credit. On
a defined-risk structure max loss is a fixed, known, spread-independent number, so
"exit at 60% of the worst case" means the same thing on a wide day and a tight one. A
multiple of a spread-crossed credit means nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import AgentConfig
from .models import OptionChain, Right


def mark_to_market(
    chain: OptionChain, short_put: float, long_put: float, short_call: float, long_call: float
) -> float | None:
    """Mid value of the short condor, per share. What it would cost to close at fair value.

    Markability follows `close_cost`'s asymmetry exactly, and for the same reason.

    A leg is priceable when it has a real *offer*; a zero bid is not a missing market,
    it is a contract that has decayed to nearly nothing, which late in a 0DTE session
    is the normal and welcome case. Requiring a two-sided market on the shorts would
    make the position unmarkable precisely when it is winning, and an unmarkable
    position is one the profit target can never fire on.

    Returns None only when a short cannot be priced at all — the leg we must buy back.
    A long with no offer is valued at zero, which understates what we hold and so
    overstates the mark: the conservative direction.
    """
    legs = {
        "sp": chain.at_strike(Right.PUT, short_put),
        "lp": chain.at_strike(Right.PUT, long_put),
        "sc": chain.at_strike(Right.CALL, short_call),
        "lc": chain.at_strike(Right.CALL, long_call),
    }
    if any(legs[k] is None or legs[k].ask <= 0 for k in ("sp", "sc")):
        return None
    long_value = sum(
        legs[k].mid for k in ("lp", "lc") if legs[k] is not None and legs[k].ask > 0
    )
    return legs["sp"].mid + legs["sc"].mid - long_value


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str
    mark: float | None
    target: float
    stop: float

    def __str__(self) -> str:
        mark = f"{self.mark:.3f}" if self.mark is not None else "n/a"
        return f"mark {mark} against target {self.target:.3f} / stop {self.stop:.3f}"


def thresholds(
    credit: float, max_loss_per_contract: float, config: AgentConfig
) -> tuple[float, float]:
    """Target and stop, both as a mid mark in dollars-per-share.

    Target: keep `profit_target` of the credit, so buy it back for the rest.
    Stop: the mark at which unrealised loss reaches `stop_loss_fraction` of max loss.
    """
    target = credit * (1.0 - config.exits.profit_target)
    stop = credit + config.exits.stop_loss_fraction * max_loss_per_contract / 100.0
    return target, stop


def decide_exit(
    mark: float | None,
    credit: float,
    max_loss_per_contract: float,
    config: AgentConfig,
    past_force_flat: bool,
) -> ExitDecision:
    """The whole exit policy, in one place, for both code paths.

    Force-flat wins over everything, including a missing mark: past the flatten time an
    unpriceable structure is a reason to get out at any price, not a reason to wait.
    """
    target, stop = thresholds(credit, max_loss_per_contract, config)

    if past_force_flat:
        return ExitDecision(True, "force_flat", mark, target, stop)
    if mark is None:
        return ExitDecision(False, "unmarkable", None, target, stop)
    if mark <= target:
        return ExitDecision(True, "profit_target", mark, target, stop)
    if mark >= stop:
        return ExitDecision(True, "stop", mark, target, stop)
    return ExitDecision(False, "hold", mark, target, stop)
