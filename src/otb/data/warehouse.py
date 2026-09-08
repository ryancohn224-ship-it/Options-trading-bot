"""A folder of Parquet files plus DuckDB/Polars to query them. No server.

Layout:
  <root>/option_chains/asof=YYYY-MM-DD/underlying=SYM/data.parquet
  <root>/equity_bars/symbol=SYM/data.parquet
  <root>/events/data.parquet
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl

from .schema import BARS_SCHEMA, CHAIN_SCHEMA, EVENTS_SCHEMA, conform, empty


class Warehouse:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "option_chains").mkdir(parents=True, exist_ok=True)
        (self.root / "equity_bars").mkdir(parents=True, exist_ok=True)
        (self.root / "events").mkdir(parents=True, exist_ok=True)

    # ---------- chains ----------
    def write_chain(self, df: pl.DataFrame) -> int:
        df = conform(df, CHAIN_SCHEMA)
        n = 0
        for (asof, und), part in df.group_by(["asof", "underlying"], maintain_order=True):
            p = self.root / "option_chains" / f"asof={asof:%Y-%m-%d}" / f"underlying={und}"
            p.mkdir(parents=True, exist_ok=True)
            part.drop(["asof", "underlying"]).write_parquet(p / "data.parquet")
            n += len(part)
        return n

    def chain_dates(self, underlying: str | None = None) -> list[date]:
        out = set()
        for d in (self.root / "option_chains").glob("asof=*"):
            if underlying is None or (d / f"underlying={underlying}").exists():
                out.add(date.fromisoformat(d.name.split("=", 1)[1]))
        return sorted(out)

    def read_chain(
        self, asof: date, underlying: str | None = None, knowable_before: datetime | None = None
    ) -> pl.DataFrame:
        base = self.root / "option_chains" / f"asof={asof:%Y-%m-%d}"
        if not base.exists():
            return empty(CHAIN_SCHEMA)
        pattern = (
            f"underlying={underlying}/data.parquet" if underlying else "underlying=*/data.parquet"
        )
        files = sorted(base.glob(pattern))
        if not files:
            return empty(CHAIN_SCHEMA)
        frames = []
        for f in files:
            und = f.parent.name.split("=", 1)[1]
            frames.append(
                pl.read_parquet(f).with_columns(
                    pl.lit(asof).alias("asof"), pl.lit(und).alias("underlying")
                )
            )
        df = conform(pl.concat(frames, how="vertical_relaxed"), CHAIN_SCHEMA)
        if knowable_before is not None:
            df = df.filter(pl.col("knowable_at") <= knowable_before)
        return df

    # ---------- bars ----------
    def write_bars(self, df: pl.DataFrame) -> int:
        df = conform(df, BARS_SCHEMA)
        n = 0
        for (sym,), part in df.group_by(["symbol"], maintain_order=True):
            p = self.root / "equity_bars" / f"symbol={sym}"
            p.mkdir(parents=True, exist_ok=True)
            f = p / "data.parquet"
            if f.exists():
                old = pl.read_parquet(f).with_columns(pl.lit(sym).alias("symbol"))
                part = (
                    pl.concat([conform(old, BARS_SCHEMA), part], how="vertical_relaxed")
                    .unique("asof", keep="last")
                    .sort("asof")
                )
            part.drop("symbol").write_parquet(f)
            n += len(part)
        return n

    def read_bars(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
        knowable_before: datetime | None = None,
    ) -> pl.DataFrame:
        f = self.root / "equity_bars" / f"symbol={symbol}" / "data.parquet"
        if not f.exists():
            return empty(BARS_SCHEMA)
        df = conform(pl.read_parquet(f).with_columns(pl.lit(symbol).alias("symbol")), BARS_SCHEMA)
        if start:
            df = df.filter(pl.col("asof") >= start)
        if end:
            df = df.filter(pl.col("asof") <= end)
        if knowable_before is not None:
            df = df.filter(pl.col("knowable_at") <= knowable_before)
        return df.sort("asof")

    # ---------- events ----------
    def write_events(self, df: pl.DataFrame) -> int:
        df = conform(df, EVENTS_SCHEMA)
        f = self.root / "events" / "data.parquet"
        if f.exists():
            df = pl.concat([conform(pl.read_parquet(f), EVENTS_SCHEMA), df]).unique(
                ["symbol", "event_type", "event_date"], keep="last"
            )
        df.write_parquet(f)
        return len(df)

    def read_events(self, knowable_before: datetime | None = None) -> pl.DataFrame:
        f = self.root / "events" / "data.parquet"
        if not f.exists():
            return empty(EVENTS_SCHEMA)
        df = conform(pl.read_parquet(f), EVENTS_SCHEMA)
        if knowable_before is not None:
            df = df.filter(pl.col("knowable_at") <= knowable_before)
        return df

    # ---------- SQL ----------
    def sql(self, query: str) -> pl.DataFrame:
        """Ad-hoc SQL over the warehouse. Tables: chains, bars, events."""
        con = duckdb.connect()
        r = str(self.root)
        con.execute(
            f"CREATE VIEW chains AS SELECT * FROM read_parquet('{r}/option_chains/*/*/data.parquet', hive_partitioning=true)"
        )
        con.execute(
            f"CREATE VIEW bars AS SELECT * FROM read_parquet('{r}/equity_bars/*/data.parquet', hive_partitioning=true)"
        )
        if (self.root / "events" / "data.parquet").exists():
            con.execute(
                f"CREATE VIEW events AS SELECT * FROM read_parquet('{r}/events/data.parquet')"
            )
        return pl.from_arrow(con.execute(query).arrow())


def utcnow() -> datetime:
    return datetime.now(UTC)
