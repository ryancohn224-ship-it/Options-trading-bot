"""Alpaca market-data adapter → canonical schemas. Live snapshot mode (what the daily loader uses).

Note: theta is normalized to per-day, vega to per-vol-point, matching the synthetic generator."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import polars as pl

from ..config import Creds
from ..pricing.symbols import parse_occ
from .schema import BARS_SCHEMA, CHAIN_SCHEMA, conform


class AlpacaData:
    def __init__(self, creds: Creds, feed: str = "indicative"):
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        self.stock = StockHistoricalDataClient(creds.api_key, creds.secret_key)
        self.opt = OptionHistoricalDataClient(creds.api_key, creds.secret_key)
        self.trading = TradingClient(creds.api_key, creds.secret_key, paper=creds.paper)
        self.feed = feed

    def daily_bars(self, symbols: list[str], start: date, end: date | None = None) -> pl.DataFrame:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=datetime.combine(start, datetime.min.time()),
            end=datetime.combine(end, datetime.max.time()) if end else None,
        )
        bars = self.opt_none(self.stock.get_stock_bars(req))
        rows = []
        for sym, lst in bars.data.items():
            for b in lst:
                ts = b.timestamp.astimezone(UTC)
                asof = ts.date()
                rows.append(
                    (
                        asof,
                        datetime.combine(asof, datetime.min.time(), tzinfo=UTC)
                        + timedelta(hours=21),
                        sym,
                        float(b.open),
                        float(b.high),
                        float(b.low),
                        float(b.close),
                        int(b.volume),
                    )
                )
        return conform(
            pl.DataFrame(rows, schema=list(BARS_SCHEMA.keys()), orient="row"), BARS_SCHEMA
        )

    @staticmethod
    def opt_none(x):
        return x

    def spot(self, symbol: str) -> float:
        from alpaca.data.requests import StockLatestTradeRequest

        t = self.stock.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))
        return float(t[symbol].price)

    def open_interest(self, underlying: str, exp_gte: date, exp_lte: date) -> dict[str, int]:
        from alpaca.trading.requests import GetOptionContractsRequest

        out, token = {}, None
        while True:
            req = GetOptionContractsRequest(
                underlying_symbols=[underlying],
                expiration_date_gte=exp_gte,
                expiration_date_lte=exp_lte,
                limit=10000,
                page_token=token,
            )
            resp = self.trading.get_option_contracts(req)
            for c in resp.option_contracts or []:
                out[c.symbol] = int(c.open_interest or 0)
            token = resp.next_page_token
            if not token:
                break
        return out

    def chain(
        self, underlying: str, asof: date | None = None, min_dte: int = 1, max_dte: int = 70
    ) -> pl.DataFrame:
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.requests import OptionChainRequest

        asof = asof or datetime.now(UTC).date()
        spot = self.spot(underlying)
        req = OptionChainRequest(
            underlying_symbol=underlying,
            feed=OptionsFeed(self.feed),
            expiration_date_gte=asof + timedelta(days=min_dte),
            expiration_date_lte=asof + timedelta(days=max_dte),
        )
        snaps = self.opt.get_option_chain(req)
        oi = self.open_interest(
            underlying, asof + timedelta(days=min_dte), asof + timedelta(days=max_dte)
        )
        now = datetime.now(UTC)
        rows = []
        for sym, s in snaps.items():
            k = parse_occ(sym)
            q = s.latest_quote
            if q is None:
                continue
            bid, ask = float(q.bid_price or 0), float(q.ask_price or 0)
            mid = 0.5 * (bid + ask) if ask > 0 else 0.0
            last = float(s.latest_trade.price) if s.latest_trade else mid
            g = s.greeks
            rows.append(
                (
                    asof,
                    now,
                    underlying,
                    sym,
                    k.expiry,
                    k.strike,
                    k.is_call,
                    bid,
                    ask,
                    mid,
                    last,
                    0,
                    oi.get(sym, 0),
                    float(s.implied_volatility) if s.implied_volatility is not None else None,
                    float(g.delta) if g and g.delta is not None else None,
                    float(g.gamma) if g and g.gamma is not None else None,
                    float(g.theta) if g and g.theta is not None else None,
                    float(g.vega) if g and g.vega is not None else None,
                    spot,
                    (k.expiry - asof).days,
                )
            )
        return conform(
            pl.DataFrame(rows, schema=list(CHAIN_SCHEMA.keys()), orient="row"), CHAIN_SCHEMA
        )
