import json
from datetime import date

from trading_agent.state import AgentState, OpenPosition, Store


def test_rolling_to_a_new_day_resets_the_daily_counters(tmp_path):
    state = AgentState(last_session_date="2026-08-17", entries_today=1, realized_pnl_today=-120)
    state.roll_to(date(2026, 8, 18))
    assert state.entries_today == 0
    assert state.realized_pnl_today == 0.0
    assert state.history[-1] == {"date": "2026-08-17", "entries": 1, "realized_pnl": -120.0}


def test_rolling_twice_in_a_day_is_a_no_op():
    """Every cron invocation rolls; only the first one of the day may count anything."""
    state = AgentState(last_session_date="2026-08-18", entries_today=1)
    state.roll_to(date(2026, 8, 18))
    state.roll_to(date(2026, 8, 18))
    assert state.entries_today == 1
    assert state.history == []


def test_a_losing_day_extends_the_streak_and_a_winning_day_clears_it():
    state = AgentState(last_session_date="2026-08-17", realized_pnl_today=-50)
    state.roll_to(date(2026, 8, 18))
    assert state.consecutive_loss_days == 1
    state.realized_pnl_today = 90
    state.roll_to(date(2026, 8, 19))
    assert state.consecutive_loss_days == 0


def test_a_flat_day_leaves_the_streak_alone():
    state = AgentState(last_session_date="2026-08-17", consecutive_loss_days=2)
    state.roll_to(date(2026, 8, 18))
    assert state.consecutive_loss_days == 2


def test_a_daily_loss_halt_expires_with_its_day(config):
    state = AgentState(last_session_date="2026-08-17")
    state.trip("daily_loss: -800 through a -750 floor")
    state.roll_to(date(2026, 8, 18))
    assert state.kill_switch is None


def test_a_manual_halt_survives_the_day_roll():
    """Anything a human set stays set until a human unsets it."""
    state = AgentState(last_session_date="2026-08-17")
    state.trip("manual: strategy under review")
    state.roll_to(date(2026, 8, 18))
    assert state.kill_switch == "manual: strategy under review"


def test_history_is_bounded():
    state = AgentState()
    for day in range(1, 200):
        state.last_session_date = f"2026-01-{day:03d}"
        state.roll_to(date(2026, 8, 18) if day % 2 else date(2026, 8, 19))
    assert len(state.history) <= 90


def test_state_round_trips_through_disk(tmp_path):
    store = Store(tmp_path / "state")
    state = AgentState(entries_today=1, consecutive_loss_days=2, realized_pnl_today=-33.5)
    store.save_state(state)
    assert store.load_state() == state


def test_position_round_trips_and_clears(tmp_path):
    store = Store(tmp_path / "state")
    assert store.load_position() is None
    position = OpenPosition(
        session_date="2026-08-18", underlying="SPY", expiry="2026-08-18", contracts=2,
        credit_per_contract=0.18, short_put=598, long_put=597, short_call=602,
        long_call=603, entry_order_id="o1", entry_time="10:15", max_loss_per_contract=82,
    )
    store.save_position(position)
    assert store.load_position() == position
    assert store.load_position().credit_dollars == 36.0
    store.save_position(None)
    assert store.load_position() is None


def test_writes_are_atomic_enough_to_leave_no_partial_file(tmp_path):
    store = Store(tmp_path / "state")
    store.save_state(AgentState(entries_today=1))
    json.loads(store.state_path.read_text())
    assert not list((tmp_path / "state").glob("*.tmp"))
