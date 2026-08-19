"""The daily sequence.

    preflight → chain → select → gate → size → review → ladder → manage → flat → journal

Every step can end the session, and ending the session without a trade is a normal,
frequently-correct outcome. The sequence is written so that each stage can only ever
*narrow* what happens next: no later stage can restore a trade an earlier one rejected.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import datetime

from . import blackscholes as bs
from . import condor as condor_mod
from . import journal as journal_mod
from . import llm as llm_mod
from .broker import Broker
from .clock import Clock
from .config import AgentConfig
from .guardrails import check_daily_loss, preflight
from .marketdata import ChainSource
from .models import CondorQuote, OptionChain
from .sizing import size_condor
from .state import OpenPosition, Store


@dataclass
class SessionResult:
    outcome: str
    detail: str
    entry: journal_mod.JournalEntry


def close_cost(chain: OptionChain, position: OpenPosition) -> float | None:
    """Conservative per-contract debit to get flat: pay the ask, hit the bid.

    The two sides are not symmetric, and treating them as such is what makes a naive
    version of this get stuck late in the day:

    * The **shorts** must be bought back, so a short with no offer is a genuine
      blocker — there is no price at which we can be sure of getting flat.
    * The **longs** are being sold. A far-out wing whose bid has decayed to nothing
      is completely normal at 0DTE; valuing it at zero is conservative and lets the
      exit proceed. Refusing to close because a worthless wing has no bid would strand
      the short legs into settlement, which is the one outcome worth any amount of
      slippage to avoid.
    """
    from .models import Right

    shorts = (
        chain.at_strike(Right.PUT, position.short_put),
        chain.at_strike(Right.CALL, position.short_call),
    )
    longs = (
        chain.at_strike(Right.PUT, position.long_put),
        chain.at_strike(Right.CALL, position.long_call),
    )
    if any(q is None or q.ask <= 0 for q in shorts):
        return None
    buy_back = sum(q.ask for q in shorts)
    sell_out = sum(q.bid for q in longs if q is not None and q.bid > 0)
    return buy_back - sell_out


class Session:
    def __init__(
        self,
        config: AgentConfig,
        clock: Clock,
        source: ChainSource,
        broker: Broker,
        store: Store,
        journal: journal_mod.Journal,
        env: dict[str, str],
        sleep=_time.sleep,
    ) -> None:
        self.config = config
        self.clock = clock
        self.source = source
        self.broker = broker
        self.store = store
        self.journal = journal
        self.env = env
        self.sleep = sleep

    # -- entry -------------------------------------------------------------------

    def open_position(self) -> SessionResult:
        cfg = self.config
        now = self.clock.now()
        entry = journal_mod.new_entry(self.clock.today(), cfg.mode, cfg.hash, now)
        entry.underlying = cfg.market.underlying

        state = self.store.load_state()
        state.roll_to(self.clock.today())

        account = None
        try:
            account = self.broker.get_account()
        except Exception as exc:  # noqa: BLE001 - a broker outage is a stand-down, not a crash
            entry.note(f"could not read the account: {exc}")

        held = self.store.load_position()
        broker_open = None
        try:
            broker_open = self.broker.is_trading_day(self.clock.today())
        except Exception as exc:  # noqa: BLE001
            entry.note(f"broker calendar unavailable, using the local one: {exc}")

        pre = preflight(
            cfg, self.clock, state, account, self.env, broker_open, holding_position=held is not None
        )
        entry.preflight = journal_mod.gates_to_rows(pre.report)
        if not pre.ok:
            self.store.save_state(state)
            return self._finish(entry, "halted" if pre.halted else "no_trade", pre.report.reason)

        expiry = self.clock.target_expiry()
        entry.expiry = expiry.isoformat()
        try:
            chain = self.source.get_chain(cfg.market.underlying, expiry)
            prev_close = self.source.previous_close(cfg.market.underlying)
            realized = self.source.realized_vol(
                cfg.market.underlying, cfg.gate.realized_vol_lookback
            )
        except Exception as exc:  # noqa: BLE001
            self.store.save_state(state)
            entry.note(f"chain fetch failed: {exc}")
            return self._finish(entry, "error", f"chain fetch failed: {exc}")

        t = self.clock.year_fraction_to(expiry)
        entry.spot = chain.spot
        entry.prev_close = prev_close
        iv = condor_mod.atm_iv(chain, t, cfg.market.risk_free_rate)
        entry.atm_iv = iv
        if iv:
            entry.expected_move = bs.expected_move(chain.spot, iv, t)

        selection = condor_mod.select_condor(chain, cfg, t)
        entry.selection = selection.reason
        if not selection.ok:
            self.store.save_state(state)
            return self._finish(entry, "no_trade", selection.reason)

        quote = selection.condor
        entry.structure = _structure_row(quote)

        entry.realized_vol = realized
        report = condor_mod.run_gates(quote, cfg, t, iv, prev_close, realized)
        entry.gates = journal_mod.gates_to_rows(report)
        if not report.passed:
            self.store.save_state(state)
            return self._finish(entry, "no_trade", report.reason)

        size = size_condor(quote, account, cfg)
        entry.sizing = size.reason
        if not size.ok:
            self.store.save_state(state)
            return self._finish(entry, "no_trade", size.reason)

        verdict = llm_mod.review(_llm_context(entry, quote, size.contracts), cfg)
        if verdict is not None:
            entry.llm = verdict.as_dict()
            if verdict.stand_down:
                self.store.save_state(state)
                return self._finish(entry, "no_trade", f"model veto: {verdict.reason}")

        filled = self._ladder_in(quote, size.contracts, entry)
        if filled is None:
            self.store.save_state(state)
            return self._finish(entry, "no_trade", "no fill at or above the gated credit")

        credit, order_id = filled
        position = OpenPosition(
            session_date=self.clock.today().isoformat(),
            underlying=cfg.market.underlying,
            expiry=expiry.isoformat(),
            contracts=size.contracts,
            credit_per_contract=credit,
            short_put=quote.short_put.strike,
            long_put=quote.long_put.strike,
            short_call=quote.short_call.strike,
            long_call=quote.long_call.strike,
            entry_order_id=order_id,
            entry_time=self.clock.now().isoformat(timespec="seconds"),
            max_loss_per_contract=(quote.width - credit) * 100.0,
        )
        self.store.save_position(position)
        state.entries_today += 1
        self.store.save_state(state)

        entry.contracts = size.contracts
        entry.credit_per_contract = credit
        entry.max_loss_per_contract = position.max_loss_per_contract
        return self._finish(entry, "traded", f"filled {size.contracts} at {credit:.2f}")

    def _ladder_in(
        self, quote: CondorQuote, contracts: int, entry: journal_mod.JournalEntry
    ) -> tuple[float, str] | None:
        """Walk from the gated credit toward mid, never below the gate.

        The gate credit is the price at which this trade was judged worth doing. A
        ladder that concedes past it is not being patient, it is entering a different
        trade from the one that was approved — so `ladder_max_concession` defaults
        to zero and is bounded by the gate at every step.
        """
        exe = self.config.execution
        floor = quote.net_credit
        ceiling = max(quote.mid_credit, floor)
        span = (ceiling - floor) * exe.ladder_max_concession

        for step in range(exe.ladder_steps):
            fraction = step / max(exe.ladder_steps - 1, 1)
            limit = round(max(ceiling - span * fraction, floor), 2)
            if limit < floor - 1e-9:
                break
            order_id = self.broker.submit_open(
                quote, contracts, limit, f"{entry.session_id}-open-{step}"
            )
            status = self._await_fill(order_id, exe.ladder_step_seconds)
            entry.orders.append(
                {
                    "kind": "open",
                    "order_id": order_id,
                    "limit": limit,
                    "status": status.status,
                    "detail": f"step {step + 1}/{exe.ladder_steps}",
                }
            )
            if status.is_filled:
                net = status.net_price if status.net_price is not None else limit
                return abs(net), order_id
            self.broker.cancel(order_id)
        return None

    def _await_fill(self, order_id: str, seconds: int):
        deadline = seconds
        status = self.broker.poll_order(order_id)
        while not status.is_filled and not status.is_dead and deadline > 0:
            self.sleep(min(2, deadline))
            deadline -= 2
            status = self.broker.poll_order(order_id)
        return status

    # -- management --------------------------------------------------------------

    def manage(self) -> SessionResult:
        cfg = self.config
        now = self.clock.now()
        entry = journal_mod.new_entry(self.clock.today(), cfg.mode, cfg.hash, now)
        position = self.store.load_position()
        if position is None:
            entry.note("nothing open to manage")
            return SessionResult("flat", "no open position", entry)

        entry.underlying = position.underlying
        entry.expiry = position.expiry
        entry.contracts = position.contracts
        entry.credit_per_contract = position.credit_per_contract
        entry.max_loss_per_contract = position.max_loss_per_contract

        from datetime import date as _date

        chain = self.source.get_chain(position.underlying, _date.fromisoformat(position.expiry))
        entry.spot = chain.spot
        cost = close_cost(chain, position)
        past_flat = self.clock.past_force_flat()

        if cost is None:
            if past_flat:
                return self._exit(position, entry, None, "force_flat_unquoted")
            entry.note("a leg is unquoted; holding and re-checking")
            return SessionResult("hold", "unquoted leg", entry)

        target = position.credit_per_contract * (1.0 - cfg.exits.profit_target)
        stop = position.credit_per_contract * cfg.exits.stop_multiple

        if past_flat:
            return self._exit(position, entry, cost, "force_flat")
        if cost <= target:
            return self._exit(position, entry, cost, "profit_target")
        if cost >= stop:
            return self._exit(position, entry, cost, "stop")

        entry.note(
            f"holding: {cost:.2f} to close against a {target:.2f} target and a {stop:.2f} stop"
        )
        return SessionResult("hold", f"cost to close {cost:.2f}", entry)

    def _exit(
        self,
        position: OpenPosition,
        entry: journal_mod.JournalEntry,
        cost: float | None,
        reason: str,
    ) -> SessionResult:
        """Close the structure. Past the flat time we pay up rather than carry it.

        A 0DTE short that reaches settlement is not a position, it is an assignment
        notice. Slippage on the way out is cheaper than that every time.
        """
        exe = self.config.execution
        urgent = reason.startswith("force_flat")
        base = cost if cost is not None else position.credit_per_contract * self.config.exits.stop_multiple
        limit = round(base * (1.0 + (exe.exit_slippage_allowance if urgent else 0.0)), 2)

        order_id = self.broker.submit_close(position, limit, f"{entry.session_id}-close")
        status = self._await_fill(order_id, exe.ladder_step_seconds)
        entry.orders.append(
            {
                "kind": "close",
                "order_id": order_id,
                "limit": limit,
                "status": status.status,
                "detail": reason,
            }
        )

        if not status.is_filled:
            self.broker.cancel(order_id)
            entry.note(
                f"exit did not fill at {limit:.2f} ({reason}) — position still open, "
                "will retry on the next management pass"
            )
            if urgent:
                entry.note(
                    "URGENT: past the force-flat time with an open 0DTE structure. "
                    "Escalate to a human."
                )
                # A failed force-flat is the one hold worth a journal entry of its
                # own — it is the state that turns a defined-risk trade into an
                # assignment, and it must be visible in the weekly review.
                return self._finish(entry, "error", f"force-flat unfilled at {limit:.2f}")
            return SessionResult("hold", f"exit unfilled at {limit:.2f}", entry)

        paid = abs(status.net_price) if status.net_price is not None else limit
        pnl = (position.credit_per_contract - paid) * 100.0 * position.contracts

        state = self.store.load_state()
        state.roll_to(self.clock.today())
        state.realized_pnl_today += pnl
        try:
            account = self.broker.get_account()
            tripped = check_daily_loss(state, account, self.config)
            if tripped:
                state.trip(tripped)
                entry.note(f"kill switch tripped — {tripped}")
        except Exception as exc:  # noqa: BLE001
            entry.note(f"could not re-read the account to check the daily loss floor: {exc}")
        self.store.save_state(state)
        self.store.save_position(None)

        entry.exit_reason = reason
        entry.realized_pnl = pnl
        return self._finish(entry, "traded", f"closed for {paid:.2f} ({reason}), P&L {pnl:+.2f}")

    # -- plumbing ----------------------------------------------------------------

    def _finish(self, entry: journal_mod.JournalEntry, outcome: str, detail: str) -> SessionResult:
        entry.outcome = outcome
        self.journal.write(entry)
        return SessionResult(outcome, detail, entry)


def _structure_row(quote: CondorQuote) -> dict:
    low, high = quote.breakevens
    return {
        "short_put": quote.short_put.strike,
        "long_put": quote.long_put.strike,
        "short_call": quote.short_call.strike,
        "long_call": quote.long_call.strike,
        "width": quote.width,
        "net_credit": quote.net_credit,
        "mid_credit": quote.mid_credit,
        "credit_ratio": quote.credit_ratio,
        "breakeven_low": low,
        "breakeven_high": high,
        "max_loss_per_contract": quote.max_loss_per_contract,
    }


def _llm_context(entry: journal_mod.JournalEntry, quote: CondorQuote, contracts: int) -> dict:
    return {
        "date": entry.session_date,
        "underlying": entry.underlying,
        "spot": entry.spot,
        "prev_close": entry.prev_close,
        "atm_iv": entry.atm_iv,
        "expected_move": entry.expected_move,
        "structure": _structure_row(quote),
        "contracts": contracts,
        "gates": entry.gates,
        "note": "All gates below already PASSED. You may only veto.",
    }
