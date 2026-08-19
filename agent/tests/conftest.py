from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from trading_agent.config import AgentConfig
from trading_agent.marketdata import SyntheticChainSource

TZ = ZoneInfo("America/New_York")
DAY = date(2026, 8, 18)


@pytest.fixture
def config() -> AgentConfig:
    return AgentConfig()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 18, 10, 15, tzinfo=TZ)


@pytest.fixture
def expiry_at() -> datetime:
    return datetime(2026, 8, 18, 16, 0, tzinfo=TZ)


@pytest.fixture
def source(now, expiry_at) -> SyntheticChainSource:
    return SyntheticChainSource(spot=600.0, iv=0.13, as_of=now, expiry_at=expiry_at)


@pytest.fixture
def chain(source):
    return source.get_chain("SPY", DAY)


@pytest.fixture
def t(now, expiry_at) -> float:
    return (expiry_at - now).total_seconds() / (365 * 24 * 3600)
