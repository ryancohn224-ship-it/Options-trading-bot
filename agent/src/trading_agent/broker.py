"""Broker seam.

`SimBroker` is a real participant in the design, not a stub: the daily sequence,
the limit ladder, the exit logic and the journal all run against it unchanged. The
Alpaca adapter is the only place in the codebase that knows Alpaca's sign convention
for multi-leg limit prices — a *credit* is a **negative** limit price. Getting that
backwards submits an order to pay to open a short condor, which is both the most
expensive possible bug and one that a paper account will happily fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from .models import Account, CondorQuote
from .state import OpenPosition

OPEN_ACTIONS = {"sell_to_open": ("sell", "sell_to_open"), "buy_to_open": ("buy", "buy_to_open")}


@dataclass(frozen=True)
class OrderStatus:
    order_id: str
    status: str
    filled_qty: int
    #: Signed per-contract net in dollars-per-share: positive = credit received.
    net_price: float | None

    @property
    def is_filled(self) -> bool:
        return self.status == "filled"

    @property
    def is_open(self) -> bool:
        return self.status in {"new", "accepted", "pending_new", "partially_filled"}

    @property
    def is_dead(self) -> bool:
        return self.status in {"canceled", "expired", "rejected", "done_for_day", "suspended"}


class Broker(Protocol):
    def get_account(self) -> Account: ...

    def is_trading_day(self, day: date) -> bool | None: ...

    def submit_open(
        self, condor: CondorQuote, contracts: int, limit_credit: float, client_order_id: str
    ) -> str: ...

    def submit_close(
        self, position: OpenPosition, limit_debit: float, client_order_id: str
    ) -> str: ...

    def poll_order(self, order_id: str) -> OrderStatus: ...

    def cancel(self, order_id: str) -> None: ...


# --------------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------------


class SimBroker:
    """Fills against the same conservative prices the gate used.

    Deliberately pessimistic: an order only fills if its limit is reachable without
    assuming any price improvement. A simulator that fills at mid teaches the agent
    that a 22%-of-width condor is available every day. It is not.
    """

    def __init__(
        self,
        equity: float = 25_000.0,
        buying_power: float | None = None,
        marketable_fraction: float = 0.0,
    ) -> None:
        self.equity = equity
        self.buying_power = buying_power if buying_power is not None else equity * 2
        self.marketable_fraction = marketable_fraction
        self._orders: dict[str, OrderStatus] = {}
        self._seq = 0
        #: Set by the session before each close attempt: the conservative debit to exit.
        self.close_cost: float | None = None

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"sim-{prefix}-{self._seq}"

    def get_account(self) -> Account:
        return Account(self.equity, self.buying_power, self.buying_power)

    def is_trading_day(self, day: date) -> bool | None:
        return None  # defer to the local calendar

    def submit_open(
        self, condor: CondorQuote, contracts: int, limit_credit: float, client_order_id: str
    ) -> str:
        reachable = condor.net_credit + (condor.mid_credit - condor.net_credit) * self.marketable_fraction
        order_id = self._next_id("open")
        if limit_credit <= reachable + 1e-9:
            self._orders[order_id] = OrderStatus(order_id, "filled", contracts, limit_credit)
        else:
            self._orders[order_id] = OrderStatus(order_id, "new", 0, None)
        return order_id

    def submit_close(
        self, position: OpenPosition, limit_debit: float, client_order_id: str
    ) -> str:
        order_id = self._next_id("close")
        cost = self.close_cost
        if cost is not None and limit_debit >= cost - 1e-9:
            self._orders[order_id] = OrderStatus(order_id, "filled", position.contracts, -limit_debit)
        else:
            self._orders[order_id] = OrderStatus(order_id, "new", 0, None)
        return order_id

    def poll_order(self, order_id: str) -> OrderStatus:
        return self._orders[order_id]

    def cancel(self, order_id: str) -> None:
        current = self._orders.get(order_id)
        if current and current.is_open:
            self._orders[order_id] = OrderStatus(order_id, "canceled", current.filled_qty, None)


# --------------------------------------------------------------------------------
# Alpaca
# --------------------------------------------------------------------------------


class AlpacaBroker:
    def __init__(self, api_key: str, secret_key: str, paper: bool = True) -> None:
        from alpaca.trading.client import TradingClient

        self._client = TradingClient(api_key, secret_key, paper=paper)
        self.paper = paper

    # -- reads -------------------------------------------------------------------

    def get_account(self) -> Account:
        acct = self._client.get_account()
        options_bp = getattr(acct, "options_buying_power", None)
        return Account(
            equity=float(acct.equity),
            buying_power=float(acct.buying_power),
            options_buying_power=float(options_bp) if options_bp is not None else None,
        )

    def is_trading_day(self, day: date) -> bool | None:
        from alpaca.trading.requests import GetCalendarRequest

        days = self._client.get_calendar(GetCalendarRequest(start=day, end=day))
        return bool(days) and days[0].date == day

    # -- writes ------------------------------------------------------------------

    def _legs(self, condor: CondorQuote):
        from alpaca.trading.enums import OrderSide, PositionIntent
        from alpaca.trading.requests import OptionLegRequest

        legs = []
        for leg in condor.legs:
            side, intent = OPEN_ACTIONS[leg.action]
            legs.append(
                OptionLegRequest(
                    symbol=leg.quote.symbol,
                    ratio_qty=1,
                    side=OrderSide(side),
                    position_intent=PositionIntent(intent),
                )
            )
        return legs

    def submit_open(
        self, condor: CondorQuote, contracts: int, limit_credit: float, client_order_id: str
    ) -> str:
        from alpaca.trading.enums import OrderClass, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        if limit_credit <= 0:
            raise ValueError("refusing to open a short condor for a debit")
        order = self._client.submit_order(
            LimitOrderRequest(
                qty=contracts,
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                # Negative = credit. See module docstring.
                limit_price=round(-abs(limit_credit), 2),
                legs=self._legs(condor),
                client_order_id=client_order_id,
            )
        )
        return str(order.id)

    def submit_close(
        self, position: OpenPosition, limit_debit: float, client_order_id: str
    ) -> str:
        from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

        from . import occ
        from .models import Right

        expiry = date.fromisoformat(position.expiry)
        closes = [
            (Right.PUT, position.long_put, OrderSide.SELL, PositionIntent.SELL_TO_CLOSE),
            (Right.PUT, position.short_put, OrderSide.BUY, PositionIntent.BUY_TO_CLOSE),
            (Right.CALL, position.short_call, OrderSide.BUY, PositionIntent.BUY_TO_CLOSE),
            (Right.CALL, position.long_call, OrderSide.SELL, PositionIntent.SELL_TO_CLOSE),
        ]
        legs = [
            OptionLegRequest(
                symbol=occ.build(position.underlying, expiry, right, strike),
                ratio_qty=1,
                side=side,
                position_intent=intent,
            )
            for right, strike, side, intent in closes
        ]
        order = self._client.submit_order(
            LimitOrderRequest(
                qty=position.contracts,
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                # Positive = debit: we are paying to get flat.
                limit_price=round(abs(limit_debit), 2),
                legs=legs,
                client_order_id=client_order_id,
            )
        )
        return str(order.id)

    def poll_order(self, order_id: str) -> OrderStatus:
        order = self._client.get_order_by_id(order_id)
        return OrderStatus(
            order_id=str(order.id),
            status=str(getattr(order.status, "value", order.status)),
            filled_qty=int(float(order.filled_qty or 0)),
            net_price=_net_from_order(order),
        )

    def cancel(self, order_id: str) -> None:
        self._client.cancel_order_by_id(order_id)


def _net_from_order(order) -> float | None:
    """Reconstruct the signed net price per package from the filled legs.

    Preferred over the parent order's `filled_avg_price` because the per-leg fills
    are unambiguous: sells add, buys subtract, so the sign of the result carries the
    credit/debit meaning without depending on the API's presentation choice.
    """
    legs = getattr(order, "legs", None)
    if legs:
        net = 0.0
        seen = False
        for leg in legs:
            price = getattr(leg, "filled_avg_price", None)
            if price is None:
                continue
            seen = True
            ratio = float(getattr(leg, "ratio_qty", 1) or 1)
            side = str(getattr(leg.side, "value", leg.side))
            net += (1 if side == "sell" else -1) * float(price) * ratio
        if seen:
            return net
    parent = getattr(order, "filled_avg_price", None)
    return -float(parent) if parent is not None else None
