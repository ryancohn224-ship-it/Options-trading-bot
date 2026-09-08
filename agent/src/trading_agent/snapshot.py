"""Stored market snapshots — the research asset.

The agent trades once a day. At that rate, tuning parameters on realised P&L is
fitting noise: twenty sessions is twenty data points, and any change that "helps"
across twenty points is indistinguishable from luck. The way out is to stop treating
the trade as the unit of evidence and start treating the *chain* as the unit of
evidence.

So every time the agent looks at the market — at entry, and at every management pass
— the entire option chain is written to disk. That turns one trade per day into a
complete record of what was on offer all day, which means:

* a parameter change can be re-run against every day already stored, immediately;
* a variant invented in week nine gets eight weeks of evidence the moment it exists;
* the decision and the outcome are both reconstructible, so "would it have worked"
  is a computation rather than an opinion.

Fidelity matters and is recorded per snapshot. `live` snapshots carry real top-of-book
from the OPRA feed and are the only ones permitted to promote a variant. `synthetic`
and `backfill` snapshots have modelled spreads, and a modelled spread is a free
parameter that quietly decides whether the credit gate passes — useful for generating
hypotheses, never for confirming them.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Literal

from .models import OptionChain, OptionQuote, Right

Fidelity = Literal["live", "backfill", "synthetic"]

#: Only real top-of-book may decide that one variant beats another.
PROMOTABLE_FIDELITY: frozenset[str] = frozenset({"live"})


@dataclass(frozen=True)
class Snapshot:
    """One complete look at the market, at one instant."""

    session_date: date
    as_of: datetime
    underlying: str
    expiry: date
    spot: float
    quotes: tuple[OptionQuote, ...]
    fidelity: Fidelity = "live"
    prev_close: float | None = None
    realized_vol: float | None = None

    @property
    def chain(self) -> OptionChain:
        return OptionChain(self.underlying, self.expiry, self.spot, self.as_of, self.quotes)

    def to_json(self) -> dict:
        return {
            "session_date": self.session_date.isoformat(),
            "as_of": self.as_of.isoformat(),
            "underlying": self.underlying,
            "expiry": self.expiry.isoformat(),
            "spot": self.spot,
            "fidelity": self.fidelity,
            "prev_close": self.prev_close,
            "realized_vol": self.realized_vol,
            # Positional rows rather than objects: a full 0DTE chain is ~200 contracts
            # and this file is appended to twenty times a day for years.
            "quotes": [
                [q.symbol, q.strike, q.right.value, q.bid, q.ask, q.bid_size, q.ask_size,
                 q.delta, q.iv]
                for q in self.quotes
            ],
        }

    @classmethod
    def from_json(cls, row: dict) -> "Snapshot":
        expiry = date.fromisoformat(row["expiry"])
        underlying = row["underlying"]
        quotes = tuple(
            OptionQuote(
                symbol=q[0], underlying=underlying, expiry=expiry, strike=q[1],
                right=Right(q[2]), bid=q[3], ask=q[4], bid_size=q[5], ask_size=q[6],
                delta=q[7], iv=q[8],
            )
            for q in row["quotes"]
        )
        return cls(
            session_date=date.fromisoformat(row["session_date"]),
            as_of=datetime.fromisoformat(row["as_of"]),
            underlying=underlying,
            expiry=expiry,
            spot=row["spot"],
            quotes=quotes,
            fidelity=row.get("fidelity", "live"),
            prev_close=row.get("prev_close"),
            realized_vol=row.get("realized_vol"),
        )


class SnapshotStore:
    """One gzipped JSONL file per session date, appended through the day.

    Appending gzip members rather than rewriting the file keeps each management pass
    to a few milliseconds and means a crashed process loses at most the pass it was
    in the middle of, never the day.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, day: date) -> Path:
        return self.root / f"{day.isoformat()}.jsonl.gz"

    def write(self, snapshot: Snapshot) -> Path:
        path = self.path_for(snapshot.session_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "at") as fh:
            fh.write(json.dumps(snapshot.to_json()) + "\n")
        return path

    def days(self) -> list[date]:
        if not self.root.exists():
            return []
        days = []
        for path in self.root.glob("*.jsonl.gz"):
            try:
                days.append(date.fromisoformat(path.name.split(".")[0]))
            except ValueError:
                continue
        return sorted(days)

    def load(self, day: date) -> list[Snapshot]:
        """Every snapshot for one day, in time order."""
        path = self.path_for(day)
        if not path.exists():
            return []
        with gzip.open(path, "rt") as fh:
            rows = [Snapshot.from_json(json.loads(line)) for line in fh if line.strip()]
        return sorted(rows, key=lambda s: s.as_of)

    def iter_days(self, days: list[date] | None = None) -> Iterator[tuple[date, list[Snapshot]]]:
        for day in days if days is not None else self.days():
            snapshots = self.load(day)
            if snapshots:
                yield day, snapshots

    def fidelity_of(self, day: date) -> Fidelity | None:
        snapshots = self.load(day)
        return snapshots[0].fidelity if snapshots else None


def snapshot_from_chain(
    chain: OptionChain,
    session_date: date,
    fidelity: Fidelity = "live",
    prev_close: float | None = None,
    realized_vol: float | None = None,
) -> Snapshot:
    return Snapshot(
        session_date=session_date,
        as_of=chain.as_of,
        underlying=chain.underlying,
        expiry=chain.expiry,
        spot=chain.spot,
        quotes=chain.quotes,
        fidelity=fidelity,
        prev_close=prev_close,
        realized_vol=realized_vol,
    )
