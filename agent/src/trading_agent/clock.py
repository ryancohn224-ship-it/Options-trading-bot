"""Session timing.

Every time in this system is exchange-local and timezone-aware. The server this runs
on may be in UTC, may be in Frankfurt, may move; none of that is allowed to shift the
entry window by an hour twice a year.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .blackscholes import SECONDS_PER_YEAR
from .config import AgentConfig

# Full-day US equity market holidays are broker-confirmed at runtime; this list is a
# local fallback for dry runs and tests only. `Broker.is_trading_day` is authoritative.
_FALLBACK_HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
}

# Half days close at 13:00 ET. A 0DTE structure entered at 12:30 on one of these has
# thirty minutes of life and no time to manage — the agent stands down instead.
_FALLBACK_HALF_DAYS_2026 = {date(2026, 11, 27), date(2026, 12, 24)}


class Clock:
    def __init__(self, config: AgentConfig, now: datetime | None = None) -> None:
        self.tz = ZoneInfo(config.market.timezone)
        self.config = config
        self._fixed_now = now.astimezone(self.tz) if now else None

    def now(self) -> datetime:
        return self._fixed_now or datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()

    def at(self, t: time, day: date | None = None) -> datetime:
        return datetime.combine(day or self.today(), t, tzinfo=self.tz)

    def expiry_datetime(self, expiry: date) -> datetime:
        return self.at(self.config.market.expiry_time, expiry)

    def year_fraction_to(self, expiry: date) -> float:
        """Calendar time to expiry in years. Clamped at zero, never negative."""
        seconds = (self.expiry_datetime(expiry) - self.now()).total_seconds()
        return max(seconds, 0.0) / SECONDS_PER_YEAR

    def target_expiry(self) -> date:
        """The expiry this session trades: today for 0DTE, plus N calendar days otherwise."""
        return self.today() + timedelta(days=self.config.market.dte)

    # -- windows -----------------------------------------------------------------

    def in_entry_window(self) -> bool:
        return self.config.schedule.entry_start <= self.now().time() < self.config.schedule.entry_cutoff

    def past_force_flat(self) -> bool:
        return self.now().time() >= self.config.schedule.force_flat

    def seconds_until_force_flat(self) -> float:
        return (self.at(self.config.schedule.force_flat) - self.now()).total_seconds()

    # -- calendar (fallback only; the broker calendar wins when reachable) --------

    def is_probably_trading_day(self, day: date | None = None) -> bool:
        day = day or self.today()
        return day.weekday() < 5 and day not in _FALLBACK_HOLIDAYS_2026

    def is_probably_half_day(self, day: date | None = None) -> bool:
        return (day or self.today()) in _FALLBACK_HALF_DAYS_2026
