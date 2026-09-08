"""Factor protocol. A factor scores candidates in place: it adds `scores[name]` (in vol points or an equivalent
expected-return contribution) and may adjust `confidence`. `MarketContext` carries everything factors need."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from ..features.surface import SurfaceSummary
from ..strategy.types import Candidate


@dataclass
class UnderlyingContext:
    symbol: str
    spot: float
    surface: SurfaceSummary | None
    rv_forecast: float | None  # HAR-IV forecast, annualized vol over hold horizon
    rv20: float | None
    trend: int  # -1, 0, +1
    momentum_12_1: float | None
    skew_pct: float | None  # percentile of 25d skew vs own history
    beta: float = 1.0


@dataclass
class MarketContext:
    asof: date
    underlyings: dict[str, UnderlyingContext]
    market_vol_forecast: float | None  # SPY HAR forecast
    p_high_vol: float  # regime probability
    extras: dict = field(default_factory=dict)


class Factor(Protocol):
    name: str

    def score(self, ctx: MarketContext, cands: list[Candidate]) -> None: ...
