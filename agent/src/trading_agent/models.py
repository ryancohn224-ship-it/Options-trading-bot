"""Core value types.

Everything downstream of the data source speaks these, so the strategy code never
knows whether it is looking at Alpaca, a synthetic chain, or a replayed session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class Right(str, Enum):
    CALL = "C"
    PUT = "P"


@dataclass(frozen=True)
class OptionQuote:
    """One contract's top-of-book, plus greeks when the feed supplies them."""

    symbol: str
    underlying: str
    expiry: date
    strike: float
    right: Right
    bid: float
    ask: float
    bid_size: int = 0
    ask_size: int = 0
    delta: float | None = None
    iv: float | None = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_pct(self) -> float:
        """Spread as a fraction of mid. Infinite for a zero mid, which is the point."""
        return self.spread / self.mid if self.mid > 0 else float("inf")

    @property
    def has_two_sided_market(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid


@dataclass(frozen=True)
class OptionChain:
    """A single expiry, snapshotted at one instant."""

    underlying: str
    expiry: date
    spot: float
    as_of: datetime
    quotes: tuple[OptionQuote, ...]

    def side(self, right: Right) -> list[OptionQuote]:
        return sorted((q for q in self.quotes if q.right is right), key=lambda q: q.strike)

    def at_strike(self, right: Right, strike: float) -> OptionQuote | None:
        for q in self.quotes:
            if q.right is right and abs(q.strike - strike) < 1e-9:
                return q
        return None


@dataclass(frozen=True)
class CondorLeg:
    quote: OptionQuote
    action: str  # "sell_to_open" | "buy_to_open"

    @property
    def is_short(self) -> bool:
        return self.action.startswith("sell")


@dataclass(frozen=True)
class CondorQuote:
    """A four-leg structure priced conservatively, before any gate has run."""

    short_put: OptionQuote
    long_put: OptionQuote
    short_call: OptionQuote
    long_call: OptionQuote
    spot: float

    @property
    def put_width(self) -> float:
        return self.short_put.strike - self.long_put.strike

    @property
    def call_width(self) -> float:
        return self.long_call.strike - self.short_call.strike

    @property
    def width(self) -> float:
        return max(self.put_width, self.call_width)

    @property
    def put_credit(self) -> float:
        """Credit if we hit the bid on the short and pay the ask on the long."""
        return self.short_put.bid - self.long_put.ask

    @property
    def call_credit(self) -> float:
        return self.short_call.bid - self.long_call.ask

    @property
    def net_credit(self) -> float:
        return self.put_credit + self.call_credit

    @property
    def mid_credit(self) -> float:
        """What the structure is theoretically worth. Always >= net_credit."""
        return (
            self.short_put.mid
            - self.long_put.mid
            + self.short_call.mid
            - self.long_call.mid
        )

    @property
    def max_loss_per_contract(self) -> float:
        return (self.width - self.net_credit) * 100.0

    @property
    def credit_ratio(self) -> float:
        return self.net_credit / self.width if self.width > 0 else 0.0

    @property
    def legs(self) -> tuple[CondorLeg, ...]:
        return (
            CondorLeg(self.long_put, "buy_to_open"),
            CondorLeg(self.short_put, "sell_to_open"),
            CondorLeg(self.short_call, "sell_to_open"),
            CondorLeg(self.long_call, "buy_to_open"),
        )

    @property
    def breakevens(self) -> tuple[float, float]:
        return (
            self.short_put.strike - self.net_credit,
            self.short_call.strike + self.net_credit,
        )


@dataclass(frozen=True)
class GateResult:
    """One named check. `detail` carries the numbers so the journal can show its work."""

    name: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        return f"[{'PASS' if self.passed else 'FAIL'}] {self.name}: {self.detail}"


@dataclass(frozen=True)
class GateReport:
    results: tuple[GateResult, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> tuple[GateResult, ...]:
        return tuple(r for r in self.results if not r.passed)

    @property
    def reason(self) -> str:
        return "; ".join(f"{r.name} ({r.detail})" for r in self.failures) or "all gates passed"


@dataclass(frozen=True)
class Account:
    equity: float
    buying_power: float
    options_buying_power: float | None = None


@dataclass(frozen=True)
class Fill:
    order_id: str
    filled_qty: int
    net_price: float  # positive = credit received per contract, in dollars-per-share
    status: str
