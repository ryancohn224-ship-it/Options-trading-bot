from datetime import datetime

import pytest

from trading_agent.clock import Clock
from trading_agent.guardrails import LIVE_ENV_VALUE, LIVE_ENV_VAR, check_daily_loss, preflight
from trading_agent.models import Account
from trading_agent.state import AgentState
from tests.conftest import TZ

ACCOUNT = Account(equity=25_000, buying_power=50_000)


def _preflight(config, now, state=None, env=None, **kw):
    return preflight(
        config, Clock(config, now=now), state or AgentState(), ACCOUNT, env or {}, **kw
    )


def test_clear_on_a_normal_weekday_inside_the_window(config, now):
    assert _preflight(config, now).ok


def test_entry_window_closes_after_the_cutoff(config):
    late = datetime(2026, 8, 18, 13, 0, tzinfo=TZ)
    result = _preflight(config, late)
    assert not result.ok
    assert any(r.name == "entry_window" for r in result.report.failures)


def test_weekend_is_not_a_trading_day(config):
    saturday = datetime(2026, 8, 22, 10, 15, tzinfo=TZ)
    assert any(r.name == "market_open" for r in _preflight(config, saturday).report.failures)


def test_live_mode_requires_the_environment_variable(config, now):
    live = config.model_copy(update={"mode": "live"})
    assert not _preflight(live, now).ok
    assert _preflight(live, now, env={LIVE_ENV_VAR: LIVE_ENV_VALUE}).ok


def test_live_mode_is_not_unlocked_by_a_truthy_value(config, now):
    """`export TRADING_AGENT_ALLOW_LIVE=1` must not be enough to start trading money."""
    live = config.model_copy(update={"mode": "live"})
    assert not _preflight(live, now, env={LIVE_ENV_VAR: "1"}).ok
    assert not _preflight(live, now, env={LIVE_ENV_VAR: "true"}).ok


def test_kill_switch_halts_and_is_reported_as_a_halt(config, now):
    state = AgentState()
    state.trip("daily_loss: blew through the floor")
    result = _preflight(config, now, state)
    assert not result.ok and result.halted


def test_loss_streak_halts_until_a_human_clears_it(config, now):
    state = AgentState(consecutive_loss_days=config.risk.max_consecutive_loss_days)
    result = _preflight(config, now, state)
    assert result.halted
    assert any(r.name == "loss_streak" for r in result.report.failures)


def test_one_entry_per_day(config, now):
    state = AgentState(entries_today=config.risk.max_entries_per_day)
    assert any(r.name == "entries_today" for r in _preflight(config, now, state).report.failures)


def test_an_open_position_blocks_a_second_entry(config, now):
    result = _preflight(config, now, holding_position=True)
    assert any(r.name == "flat" for r in result.report.failures)


def test_small_account_is_stopped_before_any_price_is_fetched(config, now):
    result = preflight(
        config, Clock(config, now=now), AgentState(), Account(1_000, 2_000), {}
    )
    assert any(r.name == "min_equity" for r in result.report.failures)


def test_broker_calendar_overrides_the_local_one(config, now):
    result = _preflight(config, now, broker_says_open=False)
    assert any(r.name == "market_open" for r in result.report.failures)


def test_daily_loss_check_trips_at_the_floor(config):
    state = AgentState(realized_pnl_today=-751)
    assert check_daily_loss(state, ACCOUNT, config) is not None
    assert check_daily_loss(AgentState(realized_pnl_today=-100), ACCOUNT, config) is None
