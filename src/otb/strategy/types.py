"""Shared position/candidate types used by strategy, portfolio, risk, execution, and both engines."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Literal

Kind = Literal["put_credit", "call_credit", "iron_condor"]


@dataclass
class Leg:
    symbol: str
    strike: float
    is_call: bool
    side: int  # +1 long, -1 short (per 1x structure)
    bid: float
    ask: float
    mid: float
    delta: float
    gamma: float
    theta: float  # per day, per share
    vega: float  # per vol point, per share
    iv: float
    open_interest: int = 0

    @property
    def spread(self) -> float:
        return max(self.ask - self.bid, 0.0)


@dataclass
class Candidate:
    underlying: str
    kind: Kind
    expiry: date
    dte: int
    legs: list[Leg]
    spot: float
    width: float
    credit_mid: float  # per share
    max_loss: float  # per contract, $ (width*100 - credit*100), at mid
    net_delta: float  # per contract (shares-equivalent = x100)
    net_gamma: float
    net_theta: float  # $ per day per contract
    net_vega: float  # $ per vol point per contract
    scores: dict = field(default_factory=dict)  # factor -> contribution
    expected_return: float = 0.0  # expected P&L / max_loss over the hold
    confidence: float = 1.0

    @property
    def symbols(self) -> list[str]:
        return [l.symbol for l in self.legs]

    def describe(self) -> str:
        ks = "/".join(
            f"{l.strike:g}{'C' if l.is_call else 'P'}{'+' if l.side > 0 else '-'}"
            for l in self.legs
        )
        return f"{self.underlying} {self.kind} {self.expiry} {ks} cr={self.credit_mid:.2f} ml={self.max_loss:.0f} er={self.expected_return:.3f}"


@dataclass
class Position:
    id: str
    underlying: str
    kind: str
    expiry: date
    legs: list[dict]  # [{symbol, side, strike, is_call, qty}] qty per contract signed by side
    qty: int
    open_date: date
    credit_received: float  # per share, actual fill
    entry_mid: float  # per share, mid credit at entry (reference for profit/stop rules)
    max_loss: float  # per contract $
    width: float
    config_hash: str
    beta: float = 1.0
    status: str = "open"
    close_date: date | None = None
    close_debit: float | None = None  # per share paid to close
    realized_pnl: float | None = None
    close_reason: str | None = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("expiry", "open_date", "close_date"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Position:
        d = dict(d)
        for k in ("expiry", "open_date", "close_date"):
            if d.get(k):
                d[k] = date.fromisoformat(d[k])
        return cls(**d)
