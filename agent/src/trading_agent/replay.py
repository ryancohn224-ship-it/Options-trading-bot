"""Replay a variant over stored snapshots.

This is the only honest way to ask "would that change have helped". It runs the exact
same selection, gate and sizing code the live agent runs — `condor.select_condor`,
`condor.run_gates`, `sizing.size_condor` — against a stored chain, so a variant cannot
score well in research by taking a path the live agent would never take. PLAN.md
section 4.4 rule 1 is the reason: any research code that can diverge from the live code
eventually will, and the divergence is invisible until money is gone.

Two things make 0DTE unusually tractable here. The contract settles the same session,
so terminal payoff is exact arithmetic on the settlement price rather than a model. And
because the agent snapshots the whole chain every management pass, the intraday path is
recorded rather than interpolated, so profit targets and stops fire on prices that
actually printed.

Sizing uses a fixed notional equity rather than a compounding balance. Comparing
variants on a compounded curve conflates edge with the order the trades happened to
arrive in; the leaderboard is about expectancy per trade, so every variant sizes off
the same base.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import condor as condor_mod
from .blackscholes import SECONDS_PER_YEAR
from .config import AgentConfig
from .exits import decide_exit, mark_to_market
from .models import Account, CondorQuote
from .sizing import size_condor
from .snapshot import Snapshot


@dataclass(frozen=True)
class ReplayResult:
    day: date
    variant: str
    fidelity: str
    entered: bool
    reason: str
    entry_at: datetime | None = None
    short_put: float | None = None
    long_put: float | None = None
    short_call: float | None = None
    long_call: float | None = None
    width: float | None = None
    credit: float | None = None
    contracts: int = 0
    risk: float = 0.0
    exit_at: datetime | None = None
    exit_reason: str | None = None
    exit_debit: float | None = None
    fees: float = 0.0
    pnl: float = 0.0
    max_adverse: float = 0.0
    gate_failures: tuple[str, ...] = field(default_factory=tuple)

    @property
    def r_multiple(self) -> float:
        """P&L as a multiple of the capital actually at risk.

        The comparable unit across variants: a wider-winged variant risks more per
        contract, so raw dollars flatter it for no reason other than size.
        """
        return self.pnl / self.risk if self.risk > 0 else 0.0


def settlement_pnl_per_contract(
    credit: float, short_put: float, long_put: float,
    short_call: float, long_call: float, settle: float,
) -> float:
    """Exact iron condor payoff at expiry, in dollars per contract.

    Only one side can finish in the money, and each side's loss is capped at its wing
    width. No model, no assumption — 0DTE settles the same day the agent traded it.
    """
    put_loss = min(max(short_put - settle, 0.0), short_put - long_put)
    call_loss = min(max(settle - short_call, 0.0), long_call - short_call)
    return (credit - put_loss - call_loss) * 100.0


def _year_fraction(as_of: datetime, expiry: date, config: AgentConfig) -> float:
    tz = ZoneInfo(config.market.timezone)
    expires_at = datetime.combine(expiry, config.market.expiry_time, tzinfo=tz)
    return max((expires_at - as_of).total_seconds(), 0.0) / SECONDS_PER_YEAR


def _local_time(moment: datetime, config: AgentConfig):
    return moment.astimezone(ZoneInfo(config.market.timezone)).time()


def _close_cost(snapshot: Snapshot, condor: CondorQuote) -> float | None:
    """Conservative debit to unwind, matching `session.close_cost` exactly."""
    from .models import Right

    chain = snapshot.chain
    shorts = (
        chain.at_strike(Right.PUT, condor.short_put.strike),
        chain.at_strike(Right.CALL, condor.short_call.strike),
    )
    longs = (
        chain.at_strike(Right.PUT, condor.long_put.strike),
        chain.at_strike(Right.CALL, condor.long_call.strike),
    )
    if any(q is None or q.ask <= 0 for q in shorts):
        return None
    return sum(q.ask for q in shorts) - sum(q.bid for q in longs if q is not None and q.bid > 0)


def replay_day(
    snapshots: list[Snapshot],
    config: AgentConfig,
    variant: str = "champion",
    equity: float = 25_000.0,
) -> ReplayResult:
    """One variant, one day. Returns why it stood down, or what the trade did."""
    if not snapshots:
        return ReplayResult(date.today(), variant, "unknown", False, "no snapshots")

    day = snapshots[0].session_date
    fidelity = snapshots[0].fidelity
    schedule = config.schedule

    entry_snapshots = [
        s for s in snapshots
        if schedule.entry_start <= _local_time(s.as_of, config) < schedule.entry_cutoff
    ]
    if not entry_snapshots:
        return ReplayResult(day, variant, fidelity, False, "no snapshot inside the entry window")

    snap = entry_snapshots[0]
    chain = snap.chain
    t = _year_fraction(snap.as_of, snap.expiry, config)
    iv = condor_mod.atm_iv(chain, t, config.market.risk_free_rate)

    selection = condor_mod.select_condor(chain, config, t)
    if not selection.ok:
        return ReplayResult(day, variant, fidelity, False, selection.reason, entry_at=snap.as_of)

    quote = selection.condor
    report = condor_mod.run_gates(quote, config, t, iv, snap.prev_close, snap.realized_vol)
    if not report.passed:
        return ReplayResult(
            day, variant, fidelity, False, report.reason, entry_at=snap.as_of,
            gate_failures=tuple(r.name for r in report.failures),
        )

    size = size_condor(quote, Account(equity, equity * 2, equity * 2), config)
    if not size.ok:
        return ReplayResult(day, variant, fidelity, False, size.reason, entry_at=snap.as_of)

    credit = quote.net_credit
    contracts = size.contracts
    risk = size.risk_dollars
    max_loss_per_contract = quote.max_loss_per_contract

    exit_at = exit_reason = exit_debit = None
    max_adverse = 0.0

    for later in snapshots:
        if later.as_of <= snap.as_of:
            continue
        # Identical call to the one the live agent makes, against identical inputs.
        mark = mark_to_market(
            later.chain, quote.short_put.strike, quote.long_put.strike,
            quote.short_call.strike, quote.long_call.strike,
        )
        cost = _close_cost(later, quote)
        if mark is not None:
            max_adverse = max(max_adverse, (mark - credit) * 100.0 * contracts)
        decision = decide_exit(
            mark, credit, max_loss_per_contract, config,
            _local_time(later.as_of, config) >= schedule.force_flat,
        )
        if decision.should_exit and cost is not None:
            exit_at, exit_reason, exit_debit = later.as_of, decision.reason, cost
            break

    fees_per_contract = config.costs.per_contract_leg
    if exit_reason is not None:
        pnl_per_contract = (credit - exit_debit) * 100.0
        legs = config.costs.legs_round_trip
    else:
        # No snapshot ever triggered an exit: the structure ran to settlement. The
        # final recorded spot is the settlement price for a same-day contract.
        settle = snapshots[-1].spot
        pnl_per_contract = settlement_pnl_per_contract(
            credit, quote.short_put.strike, quote.long_put.strike,
            quote.short_call.strike, quote.long_call.strike, settle,
        )
        exit_reason, exit_at = "settled", snapshots[-1].as_of
        legs = config.costs.legs_to_expiry

    fees = fees_per_contract * legs * contracts
    pnl = pnl_per_contract * contracts - fees

    return ReplayResult(
        day=day, variant=variant, fidelity=fidelity, entered=True, reason="traded",
        entry_at=snap.as_of,
        short_put=quote.short_put.strike, long_put=quote.long_put.strike,
        short_call=quote.short_call.strike, long_call=quote.long_call.strike,
        width=quote.width, credit=credit, contracts=contracts, risk=risk,
        exit_at=exit_at, exit_reason=exit_reason, exit_debit=exit_debit,
        fees=fees, pnl=pnl, max_adverse=max_adverse,
    )
