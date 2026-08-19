"""Chain sources.

`ChainSource` is the seam between the strategy and the world. `AlpacaChainSource`
talks to the real feed; `SyntheticChainSource` prices a chain off Black-Scholes so
the entire session — gates, sizing, ladder, exits, journal — can be exercised with no
API keys, no market hours, and no network. Every bug found in the synthetic path is a
bug that would otherwise have been found with money on.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime
from typing import Protocol

from . import blackscholes as bs
from . import occ
from .models import OptionChain, OptionQuote, Right


class ChainSource(Protocol):
    def get_chain(self, underlying: str, expiry: date) -> OptionChain: ...

    def previous_close(self, underlying: str) -> float | None: ...

    def realized_vol(self, underlying: str, lookback: int) -> float | None: ...


def annualized_close_to_close(closes: list[float]) -> float | None:
    """Annualized close-to-close volatility from a list of daily closes.

    The plainest possible estimator on purpose. Parkinson or Yang-Zhang would use the
    intraday range and be less noisy, but this one is easy to verify by hand, and a
    volatility estimate nobody can check is a volatility estimate nobody should gate
    real orders on.
    """
    if len(closes) < 3:
        return None
    returns = [
        math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance * 252.0)


class SyntheticChainSource:
    """A deterministic, arbitrage-free chain for dry runs and tests."""

    def __init__(
        self,
        spot: float = 600.0,
        iv: float = 0.13,
        as_of: datetime | None = None,
        expiry_at: datetime | None = None,
        strike_increment: float = 1.0,
        strikes_each_side: int = 25,
        half_spread: float = 0.02,
        size: int = 50,
        rate: float = 0.04,
        prev_close: float | None = None,
        realized: float | None = None,
        seed: int = 7,
    ) -> None:
        self.spot = spot
        self.iv = iv
        self.as_of = as_of or datetime.now().astimezone()
        self.expiry_at = expiry_at
        self.strike_increment = strike_increment
        self.strikes_each_side = strikes_each_side
        self.half_spread = half_spread
        self.size = size
        self.rate = rate
        self.prev_close = prev_close if prev_close is not None else spot
        #: Default to a realized reading comfortably below implied, so the synthetic
        #: path exercises a passing VRP gate rather than always short-circuiting on it.
        self.realized = realized if realized is not None else iv / 1.35
        self._rng = random.Random(seed)

    def _t(self, expiry: date) -> float:
        if self.expiry_at is None:
            return 4.0 / 24.0 / 365.0
        return max((self.expiry_at - self.as_of).total_seconds(), 0.0) / bs.SECONDS_PER_YEAR

    def get_chain(self, underlying: str, expiry: date) -> OptionChain:
        t = self._t(expiry)
        base = round(self.spot / self.strike_increment) * self.strike_increment
        quotes: list[OptionQuote] = []
        for i in range(-self.strikes_each_side, self.strikes_each_side + 1):
            strike = round(base + i * self.strike_increment, 4)
            if strike <= 0:
                continue
            for right in (Right.CALL, Right.PUT):
                theo = bs.price(self.spot, strike, t, self.iv, right, self.rate)
                # `max(-0.0, 0.0)` is -0.0 in Python; the `+ 0.0` normalises the sign
                # so a zero bid does not print as "-0.0" in the journal.
                bid = max(round(theo - self.half_spread, 2), 0.0) + 0.0
                ask = round(theo + self.half_spread, 2)
                quotes.append(
                    OptionQuote(
                        symbol=occ.build(underlying, expiry, right, strike),
                        underlying=underlying,
                        expiry=expiry,
                        strike=strike,
                        right=right,
                        bid=bid,
                        ask=ask,
                        bid_size=self.size,
                        ask_size=self.size,
                        delta=bs.delta(self.spot, strike, t, self.iv, right, self.rate),
                        iv=self.iv,
                    )
                )
        return OptionChain(underlying, expiry, self.spot, self.as_of, tuple(quotes))

    def previous_close(self, underlying: str) -> float | None:
        return self.prev_close

    def realized_vol(self, underlying: str, lookback: int) -> float | None:
        return self.realized


class AlpacaChainSource:
    """Live chain from Alpaca.

    The `feed` matters: the free `indicative` feed publishes neither reliable sizes
    nor greeks. The liquidity gate will reject nearly everything on it, which is the
    correct behaviour — do not "fix" that by relaxing the gate. Subscribe to OPRA.
    """

    def __init__(self, api_key: str, secret_key: str, feed: str = "opra") -> None:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.historical.stock import StockHistoricalDataClient

        self._options = OptionHistoricalDataClient(api_key, secret_key)
        self._stocks = StockHistoricalDataClient(api_key, secret_key)
        self._feed = feed

    def _spot(self, underlying: str) -> float:
        from alpaca.data.requests import StockLatestTradeRequest

        trade = self._stocks.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=underlying)
        )
        return float(trade[underlying].price)

    def get_chain(self, underlying: str, expiry: date) -> OptionChain:
        from alpaca.data.requests import OptionChainRequest

        spot = self._spot(underlying)
        snapshots = self._options.get_option_chain(
            OptionChainRequest(
                underlying_symbol=underlying,
                expiration_date=expiry,
                feed=self._feed,
            )
        )
        quotes: list[OptionQuote] = []
        for symbol, snap in snapshots.items():
            quote = getattr(snap, "latest_quote", None)
            if quote is None:
                continue
            try:
                _, sym_expiry, right, strike = occ.parse(symbol)
            except ValueError:
                continue
            if sym_expiry != expiry:
                continue
            greeks = getattr(snap, "greeks", None)
            quotes.append(
                OptionQuote(
                    symbol=symbol,
                    underlying=underlying,
                    expiry=expiry,
                    strike=strike,
                    right=right,
                    bid=float(quote.bid_price or 0.0),
                    ask=float(quote.ask_price or 0.0),
                    bid_size=int(quote.bid_size or 0),
                    ask_size=int(quote.ask_size or 0),
                    delta=float(greeks.delta) if greeks and greeks.delta is not None else None,
                    iv=(
                        float(snap.implied_volatility)
                        if getattr(snap, "implied_volatility", None)
                        else None
                    ),
                )
            )
        return OptionChain(
            underlying, expiry, spot, datetime.now().astimezone(), tuple(quotes)
        )

    def _daily_closes(self, underlying: str, limit: int) -> list[float]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        bars = self._stocks.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=underlying, timeframe=TimeFrame.Day, limit=limit
            )
        )
        return [float(bar.close) for bar in bars.data.get(underlying, [])]

    def previous_close(self, underlying: str) -> float | None:
        closes = self._daily_closes(underlying, 2)
        return closes[-2] if len(closes) >= 2 else None

    def realized_vol(self, underlying: str, lookback: int) -> float | None:
        # The final bar is today's partial session; excluding it keeps the estimate
        # from being contaminated by the move we are trying to price against.
        closes = self._daily_closes(underlying, lookback + 2)
        return annualized_close_to_close(closes[:-1])
