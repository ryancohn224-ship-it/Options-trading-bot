"""A synthetic market, for proving the research loop before real data exists.

The loop needs stored days to have anything to say, and stored days arrive one per
session. This generates them: a spot path, a volatility process, and a full chain
snapshotted every fifteen minutes, written in exactly the format the live agent writes.

**What this does and does not establish.** The world below is built with a variance
risk premium in it — implied volatility is drawn above realized on average, with enough
spread that it is sometimes below. A premium-selling strategy gated on that spread
*should* make money here, and if the research engine cannot find that, the engine is
broken. That is the whole purpose: it is a test of the measuring instrument, not of the
strategy. Nothing observed here is evidence about the real market, which is why every
snapshot is stamped `synthetic` and the promotion rules refuse to count them.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import blackscholes as bs
from . import occ
from .config import AgentConfig
from .models import OptionQuote, Right
from .snapshot import Snapshot, SnapshotStore

SNAPSHOT_EVERY = timedelta(minutes=15)


def _trading_days(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _chain_at(
    day: date, moment: datetime, spot: float, implied: float, config: AgentConfig,
    half_spread: float, strikes_each_side: int,
) -> tuple[OptionQuote, ...]:
    tz = ZoneInfo(config.market.timezone)
    expires_at = datetime.combine(day, config.market.expiry_time, tzinfo=tz)
    t = max((expires_at - moment).total_seconds(), 0.0) / bs.SECONDS_PER_YEAR
    increment = config.structure.strike_increment
    base = round(spot / increment) * increment

    quotes: list[OptionQuote] = []
    for i in range(-strikes_each_side, strikes_each_side + 1):
        strike = round(base + i * increment, 4)
        if strike <= 0:
            continue
        for right in (Right.CALL, Right.PUT):
            theo = bs.price(spot, strike, t, implied, right, config.market.risk_free_rate)
            bid = max(round(theo - half_spread, 2), 0.0) + 0.0
            ask = round(theo + half_spread, 2)
            quotes.append(
                OptionQuote(
                    symbol=occ.build(config.market.underlying, day, right, strike),
                    underlying=config.market.underlying, expiry=day, strike=strike,
                    right=right, bid=bid, ask=ask, bid_size=50, ask_size=50,
                    delta=bs.delta(spot, strike, t, implied, right, config.market.risk_free_rate)
                    if t > 0 else None,
                    iv=implied,
                )
            )
    return tuple(quotes)


def simulate(
    store: SnapshotStore,
    config: AgentConfig,
    days: int = 120,
    start: date | None = None,
    spot0: float = 600.0,
    seed: int = 4,
    vrp_mean: float = 0.12,
    vrp_sd: float = 0.22,
    half_spread: float = 0.02,
    strikes_each_side: int = 15,
) -> list[date]:
    """Write `days` synthetic sessions into the store. Returns the dates written."""
    rng = random.Random(seed)
    tz = ZoneInfo(config.market.timezone)
    session_start = time(10, 0)
    session_end = config.market.expiry_time

    spot = spot0
    closes: list[float] = []
    written: list[date] = []

    for day in _trading_days(start or date(2026, 1, 5), days):
        prev_close = spot

        # The day's realized volatility, and the implied the market is asking for it.
        # The premium is positive on average and negative often enough that a gate on
        # it has something to discriminate.
        realized_today = math.exp(rng.gauss(math.log(0.11), 0.35))
        implied = max(realized_today * (1.0 + rng.gauss(vrp_mean, vrp_sd)), 0.02)

        # Trailing realized, computed the same way the live agent computes it.
        realized_estimate = None
        if len(closes) >= 3:
            window = closes[-config.gate.realized_vol_lookback :]
            rets = [math.log(b / a) for a, b in zip(window, window[1:]) if a > 0 and b > 0]
            if len(rets) >= 2:
                mean = sum(rets) / len(rets)
                var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
                realized_estimate = math.sqrt(var * 252.0)

        moment = datetime.combine(day, session_start, tzinfo=tz)
        finish = datetime.combine(day, session_end, tzinfo=tz)
        step_years = SNAPSHOT_EVERY.total_seconds() / bs.SECONDS_PER_YEAR

        while moment <= finish:
            store.write(
                Snapshot(
                    session_date=day, as_of=moment, underlying=config.market.underlying,
                    expiry=day, spot=round(spot, 2),
                    quotes=_chain_at(day, moment, spot, implied, config, half_spread,
                                     strikes_each_side),
                    fidelity="synthetic", prev_close=round(prev_close, 2),
                    realized_vol=realized_estimate,
                )
            )
            # Diffuse the spot forward one snapshot interval at the day's true vol.
            spot *= math.exp(
                -0.5 * realized_today**2 * step_years
                + realized_today * math.sqrt(step_years) * rng.gauss(0, 1)
            )
            moment += SNAPSHOT_EVERY

        closes.append(spot)
        written.append(day)

    return written
