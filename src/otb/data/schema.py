"""Canonical schemas. Every row carries `knowable_at`: the instant this information became available.
Backtests filter on knowable_at <= decision time. This is the point-in-time guarantee."""

from __future__ import annotations

import polars as pl

CHAIN_SCHEMA = {
    "asof": pl.Date,  # trading date of the snapshot
    "knowable_at": pl.Datetime("us", "UTC"),
    "underlying": pl.Utf8,
    "symbol": pl.Utf8,  # OCC
    "expiry": pl.Date,
    "strike": pl.Float64,
    "is_call": pl.Boolean,
    "bid": pl.Float64,
    "ask": pl.Float64,
    "mid": pl.Float64,
    "last": pl.Float64,
    "volume": pl.Int64,
    "open_interest": pl.Int64,
    "iv": pl.Float64,
    "delta": pl.Float64,
    "gamma": pl.Float64,
    "theta": pl.Float64,
    "vega": pl.Float64,
    "spot": pl.Float64,
    "dte": pl.Int32,
}

BARS_SCHEMA = {
    "asof": pl.Date,
    "knowable_at": pl.Datetime("us", "UTC"),
    "symbol": pl.Utf8,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
}

EVENTS_SCHEMA = {
    "symbol": pl.Utf8,
    "event_type": pl.Utf8,  # earnings | dividend | macro
    "event_date": pl.Date,
    "knowable_at": pl.Datetime("us", "UTC"),
    "note": pl.Utf8,
}


def empty(schema: dict) -> pl.DataFrame:
    return pl.DataFrame(schema=schema)


def conform(df: pl.DataFrame, schema: dict) -> pl.DataFrame:
    """Add missing columns as null, cast, and order columns to the schema."""
    for c, t in schema.items():
        if c not in df.columns:
            df = df.with_columns(pl.lit(None).cast(t).alias(c))
    return df.select([pl.col(c).cast(t) for c, t in schema.items()])
