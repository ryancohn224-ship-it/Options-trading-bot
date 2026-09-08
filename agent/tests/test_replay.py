import pytest

from trading_agent.replay import ReplayResult, replay_day, settlement_pnl_per_contract
from trading_agent.simulate import simulate
from trading_agent.snapshot import SnapshotStore


# -- settlement arithmetic ---------------------------------------------------------


@pytest.mark.parametrize(
    "settle,expected",
    [
        (600.0, 13.0),    # finishes between the shorts: keep the whole credit
        (598.0, 13.0),    # exactly at the short put: still no loss
        (597.5, -37.0),   # halfway into the put spread
        (590.0, -87.0),   # through the long put: max loss
        (602.0, 13.0),    # exactly at the short call
        (610.0, -87.0),   # through the long call: max loss, same as the put side
    ],
)
def test_settlement_payoff(settle, expected):
    assert settlement_pnl_per_contract(0.13, 598, 597, 602, 603, settle) == pytest.approx(expected)


def test_loss_is_capped_at_the_wing_width_either_side():
    far_below = settlement_pnl_per_contract(0.13, 598, 597, 602, 603, 400.0)
    far_above = settlement_pnl_per_contract(0.13, 598, 597, 602, 603, 900.0)
    assert far_below == far_above == pytest.approx(-87.0)


# -- replaying a session -----------------------------------------------------------


@pytest.fixture
def stored(tmp_path, config):
    store = SnapshotStore(tmp_path / "snap")
    days = simulate(store, config, days=12, seed=3)
    return store, days


def test_replay_produces_one_result_per_day(stored, config):
    store, days = stored
    results = [replay_day(store.load(d), config, "champion") for d in days]
    assert len(results) == len(days)
    assert all(isinstance(r, ReplayResult) for r in results)


def test_a_traded_day_carries_a_full_record(stored, config):
    store, days = stored
    traded = [r for r in (replay_day(store.load(d), config, "c") for d in days) if r.entered]
    if not traded:
        pytest.skip("no entries in this synthetic sample")
    r = traded[0]
    assert r.long_put < r.short_put < r.short_call < r.long_call
    assert r.credit > 0 and r.contracts > 0 and r.risk > 0
    assert r.exit_reason in {"profit_target", "stop", "force_flat", "settled"}
    assert r.fees > 0, "pass-through fees must be charged on every trade"


def test_fees_scale_with_contracts_and_legs(stored, config):
    store, days = stored
    for day in days:
        r = replay_day(store.load(day), config, "c")
        if r.entered:
            expected = config.costs.per_contract_leg * config.costs.legs_round_trip * r.contracts
            assert r.fees == pytest.approx(expected) or r.exit_reason == "settled"
            return
    pytest.skip("no entries in this synthetic sample")


def test_a_stand_down_records_which_gate_refused(stored, config):
    store, days = stored
    stood = [r for r in (replay_day(store.load(d), config, "c") for d in days) if not r.entered]
    if not stood:
        pytest.skip("every day traded in this sample")
    assert stood[0].reason


def test_replay_is_deterministic(stored, config):
    store, days = stored
    first = [replay_day(store.load(d), config, "c").pnl for d in days]
    second = [replay_day(store.load(d), config, "c").pnl for d in days]
    assert first == second


def test_r_multiple_is_pnl_over_risk(stored, config):
    store, days = stored
    for day in days:
        r = replay_day(store.load(day), config, "c")
        if r.entered:
            assert r.r_multiple == pytest.approx(r.pnl / r.risk)
            return


def test_no_snapshots_is_handled(config):
    assert replay_day([], config, "c").entered is False


def test_fidelity_is_carried_onto_every_result(stored, config):
    store, days = stored
    assert replay_day(store.load(days[0]), config, "c").fidelity == "synthetic"
