from datetime import date, datetime

import pytest

from trading_agent.broker import SimBroker
from trading_agent.clock import Clock
from trading_agent.journal import Journal
from trading_agent.marketdata import SyntheticChainSource
from trading_agent.session import Session, close_cost
from trading_agent.state import OpenPosition, Store
from tests.conftest import DAY, TZ


def build(config, tmp_path, now, spot=600.0, iv=0.13, equity=25_000.0, realized=None, **kw):
    config = config.model_copy(
        update={
            "paths": config.paths.model_copy(
                update={
                    "state_dir": tmp_path / "state",
                    "journal_dir": tmp_path / "journal",
                    "kill_switch_file": tmp_path / "state" / "KILL_SWITCH",
                }
            )
        }
    )
    source = SyntheticChainSource(
        spot=spot, iv=iv, as_of=now,
        expiry_at=datetime(2026, 8, 18, 16, 0, tzinfo=TZ),
        prev_close=kw.pop("prev_close", 600.0), realized=realized,
    )
    broker = SimBroker(equity=equity)
    session = Session(
        config=config, clock=Clock(config, now=now), source=source, broker=broker,
        store=Store(config.paths.state_dir), journal=Journal(config.paths.journal_dir),
        env={}, sleep=lambda _s: None,
    )
    return session, broker


@pytest.fixture
def entry_time():
    return datetime(2026, 8, 18, 10, 15, tzinfo=TZ)


def test_a_clean_session_opens_a_position_and_records_it(config, tmp_path, entry_time):
    session, _ = build(config, tmp_path, entry_time)
    result = session.open_position()

    assert result.outcome == "traded", result.detail
    position = session.store.load_position()
    assert position is not None
    assert position.contracts > 0
    assert position.credit_per_contract > 0
    assert session.store.load_state().entries_today == 1


def test_a_failed_gate_leaves_no_position_and_still_writes_a_journal(config, tmp_path, entry_time):
    """Standing down is a full journal entry, not a silent exit."""
    session, _ = build(config, tmp_path, entry_time, realized=0.20)  # implied below realized
    result = session.open_position()

    assert result.outcome == "no_trade"
    assert "variance_risk_premium" in result.detail
    assert session.store.load_position() is None
    assert (tmp_path / "journal" / "2026-08-18.md").exists()
    assert session.store.load_state().entries_today == 0


def test_the_ladder_never_bids_below_the_gated_credit(config, tmp_path, entry_time):
    session, _ = build(config, tmp_path, entry_time)
    result = session.open_position()
    gate_credit = result.entry.structure["net_credit"]
    limits = [o["limit"] for o in result.entry.orders]

    assert limits == sorted(limits, reverse=True), "the ladder must concede, not improve"
    assert min(limits) >= round(gate_credit, 2) - 1e-9


def test_an_unfillable_ladder_ends_as_no_trade(config, tmp_path, entry_time):
    """Nothing at or above the gated credit means no trade, not a worse trade."""
    strict = config.model_copy(
        update={"execution": config.execution.model_copy(update={"ladder_max_concession": 0.0})}
    )
    session, _ = build(strict, tmp_path, entry_time)
    result = session.open_position()
    assert result.outcome == "no_trade"
    assert session.store.load_position() is None


def test_preflight_blocks_the_session_before_any_chain_is_fetched(config, tmp_path):
    class Exploding:
        def get_chain(self, *a):
            raise AssertionError("must not fetch a chain after preflight fails")

        def previous_close(self, *a):
            raise AssertionError("must not fetch a close after preflight fails")

        def realized_vol(self, *a):
            raise AssertionError("must not fetch vol after preflight fails")

    session, _ = build(config, tmp_path, datetime(2026, 8, 18, 14, 0, tzinfo=TZ))
    session.source = Exploding()
    assert session.open_position().outcome == "no_trade"


def test_a_second_entry_the_same_day_is_refused(config, tmp_path, entry_time):
    session, _ = build(config, tmp_path, entry_time)
    assert session.open_position().outcome == "traded"

    again, _ = build(config, tmp_path, entry_time)
    result = again.open_position()
    assert result.outcome == "no_trade"
    assert "flat" in result.detail


# -- exits -------------------------------------------------------------------------


def _open_then(config, tmp_path, entry_time, later, spot=600.0, iv=0.13):
    session, broker = build(config, tmp_path, entry_time)
    assert session.open_position().outcome == "traded"
    manage_session, manage_broker = build(config, tmp_path, later, spot=spot, iv=iv)
    manage_broker._seq = 100
    position = manage_session.store.load_position()
    chain = manage_session.source.get_chain("SPY", DAY)
    manage_broker.close_cost = close_cost(chain, position)
    return manage_session, manage_broker


def test_profit_target_closes_the_position_and_books_the_pnl(config, tmp_path, entry_time):
    session, _ = _open_then(config, tmp_path, entry_time, datetime(2026, 8, 18, 15, 15, tzinfo=TZ))
    result = session.manage()

    assert result.outcome == "traded"
    assert result.entry.exit_reason == "profit_target"
    assert result.entry.realized_pnl > 0
    assert session.store.load_position() is None
    assert session.store.load_state().realized_pnl_today > 0


def test_force_flat_closes_even_when_no_other_rule_has_fired(config, tmp_path, entry_time):
    session, _ = _open_then(
        config, tmp_path, entry_time, datetime(2026, 8, 18, 15, 50, tzinfo=TZ), spot=599.0
    )
    result = session.manage()
    assert result.entry.exit_reason == "force_flat"
    assert session.store.load_position() is None


def test_an_unfilled_force_flat_is_journalled_as_an_error(config, tmp_path, entry_time):
    """The one outcome that must never be quiet: past the flat time, still short."""
    session, broker = _open_then(
        config, tmp_path, entry_time, datetime(2026, 8, 18, 15, 50, tzinfo=TZ)
    )
    broker.close_cost = 99.0  # nothing we bid will reach it
    result = session.manage()

    assert result.outcome == "error"
    assert session.store.load_position() is not None
    assert any("URGENT" in note for note in result.entry.notes)


def test_holding_when_neither_target_nor_stop_is_hit(config, tmp_path, entry_time):
    session, broker = _open_then(
        config, tmp_path, entry_time, datetime(2026, 8, 18, 11, 15, tzinfo=TZ)
    )
    result = session.manage()
    assert result.outcome == "hold"
    assert session.store.load_position() is not None


def test_managing_with_nothing_open_is_a_no_op(config, tmp_path, entry_time):
    session, _ = build(config, tmp_path, entry_time)
    assert session.manage().outcome == "flat"


# -- close cost --------------------------------------------------------------------


def _position():
    return OpenPosition(
        session_date="2026-08-18", underlying="SPY", expiry="2026-08-18", contracts=1,
        credit_per_contract=0.13, short_put=598, long_put=597, short_call=602,
        long_call=603, entry_order_id="o", entry_time="10:15", max_loss_per_contract=87,
    )


def _chain_with(chain, replacements):
    from trading_agent.models import OptionChain

    quotes = tuple(replacements.get((q.right, q.strike), q) for q in chain.quotes)
    return OptionChain(chain.underlying, chain.expiry, chain.spot, chain.as_of, quotes)


def test_a_worthless_long_wing_does_not_block_the_exit(chain):
    """Normal at 0DTE. Valuing the wing at zero is conservative and lets us get flat."""
    from trading_agent.models import OptionQuote, Right

    dead = chain.at_strike(Right.PUT, 597.0)
    zeroed = OptionQuote(
        dead.symbol, dead.underlying, dead.expiry, dead.strike, dead.right,
        0.0, 0.02, 0, 50, dead.delta, dead.iv,
    )
    cost = close_cost(_chain_with(chain, {(Right.PUT, 597.0): zeroed}), _position())
    assert cost is not None and cost > 0


def test_a_short_with_no_offer_does_block_the_exit(chain):
    """There is no price at which we can be sure of buying it back, so we do not pretend."""
    from trading_agent.models import OptionQuote, Right

    q = chain.at_strike(Right.CALL, 602.0)
    unoffered = OptionQuote(
        q.symbol, q.underlying, q.expiry, q.strike, q.right, 0.05, 0.0, 50, 0, q.delta, q.iv
    )
    assert close_cost(_chain_with(chain, {(Right.CALL, 602.0): unoffered}), _position()) is None


# -- the model's seat --------------------------------------------------------------


def test_a_model_veto_cancels_an_otherwise_approved_trade(config, tmp_path, entry_time, monkeypatch):
    from trading_agent import llm as llm_mod

    monkeypatch.setattr(
        llm_mod, "review",
        lambda ctx, cfg: llm_mod.Verdict(True, "FOMC at 14:00", "Standing down."),
    )
    session, _ = build(config, tmp_path, entry_time)
    result = session.open_position()

    assert result.outcome == "no_trade"
    assert "model veto" in result.detail
    assert session.store.load_position() is None


def test_a_model_approval_does_not_override_a_failed_gate(config, tmp_path, entry_time, monkeypatch):
    """The model is only ever consulted after the gates pass. It cannot revive a no."""
    from trading_agent import llm as llm_mod

    called = []
    monkeypatch.setattr(
        llm_mod, "review",
        lambda ctx, cfg: called.append(ctx) or llm_mod.Verdict(False, "looks fine", ""),
    )
    session, _ = build(config, tmp_path, entry_time, realized=0.20)
    assert session.open_position().outcome == "no_trade"
    assert called == [], "the model must not even be asked about a rejected trade"
