"""Broker protocol shared by the simulator and Alpaca. The strategy engine only talks to this."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import polars as pl

from ..strategy.types import Candidate, Position


@dataclass
class Fill:
    ok: bool
    price_per_share: float  # credit received (open) or debit paid (close), per share
    mid_at_submit: float
    detail: str = ""


@dataclass
class AccountSnapshot:
    equity: float
    cash: float
    buying_power: float


class Broker(Protocol):
    def account(self, chain: pl.DataFrame | None = None) -> AccountSnapshot: ...
    def open_positions(self) -> list[Position]: ...
    def open(
        self,
        cand: Candidate,
        qty: int,
        today: date,
        config_hash: str,
        beta: float,
        chain: pl.DataFrame | None = None,
    ) -> tuple[Fill, Position | None]: ...
    def close(
        self, pos: Position, today: date, reason: str, chain: pl.DataFrame | None = None
    ) -> Fill: ...
