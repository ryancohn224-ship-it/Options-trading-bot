"""Exit policy, led by the regression test for the bug that motivated this module."""

import pytest

from trading_agent.condor import select_condor
from trading_agent.exits import decide_exit, mark_to_market, thresholds
from trading_agent.session import close_cost
from trading_agent.state import OpenPosition


def _position(credit=0.13, max_loss=87.0):
    return OpenPosition(
        session_date="2026-08-18", underlying="SPY", expiry="2026-08-18", contracts=1,
        credit_per_contract=credit, short_put=598, long_put=597, short_call=602,
        long_call=603, entry_order_id="o", entry_time="10:15", max_loss_per_contract=max_loss,
    )


def test_the_stop_is_not_inside_the_bid_ask_spread(chain, config, t):
    """The original bug: every trade stopped out at the instant it opened.

    The stop was a multiple of the spread-crossed credit and was compared against the
    spread-crossed cost to close, so it sat a full round trip below where the position
    started. This asserts the gap that made that possible is gone.
    """
    quote = select_condor(chain, config, t).condor
    credit = quote.net_credit
    _, stop = thresholds(credit, quote.max_loss_per_contract, config)

    mark_at_entry = mark_to_market(
        chain, quote.short_put.strike, quote.long_put.strike,
        quote.short_call.strike, quote.long_call.strike,
    )
    assert mark_at_entry < stop, (
        f"mark at entry {mark_at_entry:.3f} is already past the stop {stop:.3f} — "
        "the position would exit before the market moved"
    )


def test_the_marketable_cost_really_is_worse_than_the_mark(chain, config, t):
    """Establishes the asymmetry the bug depended on, so the test above has teeth."""
    quote = select_condor(chain, config, t).condor
    mark = mark_to_market(chain, 598.0, 597.0, 602.0, 603.0)
    cost = close_cost(chain, _position())
    assert cost > mark, "crossing the spread must cost more than the mid mark"


def test_stop_is_a_fraction_of_max_loss(config):
    target, stop = thresholds(credit=0.20, max_loss_per_contract=80.0, config=config)
    # 60% of $80 max loss = $48 = 0.48/share of unrealised loss on top of the credit.
    assert stop == pytest.approx(0.20 + 0.60 * 0.80)
    assert target == pytest.approx(0.10)


def test_force_flat_beats_every_other_rule(config):
    decision = decide_exit(0.15, 0.20, 80.0, config, past_force_flat=True)
    assert decision.should_exit and decision.reason == "force_flat"


def test_force_flat_fires_even_with_no_mark(config):
    """Unpriceable past the flatten time is a reason to leave, not a reason to wait."""
    decision = decide_exit(None, 0.20, 80.0, config, past_force_flat=True)
    assert decision.should_exit and decision.reason == "force_flat"


def test_no_mark_before_the_flatten_time_holds(config):
    assert not decide_exit(None, 0.20, 80.0, config, past_force_flat=False).should_exit


def test_profit_target_and_stop_fire_on_the_mark(config):
    assert decide_exit(0.09, 0.20, 80.0, config, False).reason == "profit_target"
    assert decide_exit(0.70, 0.20, 80.0, config, False).reason == "stop"
    assert decide_exit(0.25, 0.20, 80.0, config, False).reason == "hold"


def test_a_short_with_no_offer_is_unmarkable(chain):
    from trading_agent.models import OptionChain, OptionQuote, Right

    q = chain.at_strike(Right.CALL, 602.0)
    dead = OptionQuote(q.symbol, q.underlying, q.expiry, q.strike, q.right,
                       0.05, 0.0, 50, 0, q.delta, q.iv)
    damaged = OptionChain(
        chain.underlying, chain.expiry, chain.spot, chain.as_of,
        tuple(dead if x is q else x for x in chain.quotes),
    )
    assert mark_to_market(damaged, 598, 597, 602, 603) is None


def test_a_short_with_a_zero_bid_is_still_markable(chain):
    """Late in a 0DTE session this is the winning case, not a broken one."""
    from trading_agent.models import OptionChain, OptionQuote, Right

    q = chain.at_strike(Right.CALL, 602.0)
    decayed = OptionQuote(q.symbol, q.underlying, q.expiry, q.strike, q.right,
                          0.0, 0.02, 0, 50, q.delta, q.iv)
    ok = OptionChain(
        chain.underlying, chain.expiry, chain.spot, chain.as_of,
        tuple(decayed if x is q else x for x in chain.quotes),
    )
    assert mark_to_market(ok, 598, 597, 602, 603) is not None
