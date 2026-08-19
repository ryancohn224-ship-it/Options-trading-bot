"""Adapter tests, with the sign convention first because it is the expensive one.

Alpaca expresses a multi-leg **credit as a negative limit price**. Getting this
backwards submits an order that pays to open a short condor — and a paper account
will fill it happily, so the mistake shows up as an unexplained loss rather than a
rejection. It gets a test of its own.
"""

import pytest

from trading_agent.broker import AlpacaBroker, SimBroker, _net_from_order
from trading_agent.condor import select_condor
from trading_agent.state import OpenPosition

alpaca = pytest.importorskip("alpaca", reason="alpaca-py not installed")


class FakeClient:
    def __init__(self):
        self.requests = []

    def submit_order(self, request):
        self.requests.append(request)
        return type("Order", (), {"id": "fake-order-1"})()


@pytest.fixture
def broker():
    b = object.__new__(AlpacaBroker)
    b._client = FakeClient()
    b.paper = True
    return b


@pytest.fixture
def condor(chain, config, t):
    return select_condor(chain, config, t).condor


def test_opening_a_condor_sends_a_negative_limit_price(broker, condor):
    broker.submit_open(condor, contracts=2, limit_credit=0.25, client_order_id="x")
    request = broker._client.requests[0]
    assert request.limit_price == -0.25, "a credit must be negative on the wire"
    assert request.qty == 2


def test_opening_refuses_a_negative_credit(broker, condor):
    """Defence in depth: the gate should never allow this, and the adapter still won't."""
    with pytest.raises(ValueError):
        broker.submit_open(condor, 1, limit_credit=-0.25, client_order_id="x")


def test_closing_a_condor_sends_a_positive_limit_price(broker):
    position = OpenPosition(
        session_date="2026-08-18", underlying="SPY", expiry="2026-08-18", contracts=3,
        credit_per_contract=0.20, short_put=598, long_put=597, short_call=602,
        long_call=603, entry_order_id="e", entry_time="t", max_loss_per_contract=80,
    )
    broker.submit_close(position, limit_debit=0.10, client_order_id="x")
    request = broker._client.requests[0]
    assert request.limit_price == 0.10, "a debit must be positive on the wire"


def test_opening_legs_carry_the_right_sides_and_intents(broker, condor):
    broker.submit_open(condor, 1, 0.25, "x")
    legs = broker._client.requests[0].legs
    by_symbol = {leg.symbol: leg for leg in legs}
    assert len(legs) == 4
    assert by_symbol[condor.short_put.symbol].side.value == "sell"
    assert by_symbol[condor.long_put.symbol].side.value == "buy"
    assert by_symbol[condor.short_call.symbol].side.value == "sell"
    assert by_symbol[condor.long_call.symbol].side.value == "buy"
    assert all(leg.position_intent.value.endswith("to_open") for leg in legs)


def test_closing_legs_reverse_every_side(broker):
    position = OpenPosition(
        session_date="2026-08-18", underlying="SPY", expiry="2026-08-18", contracts=1,
        credit_per_contract=0.20, short_put=598, long_put=597, short_call=602,
        long_call=603, entry_order_id="e", entry_time="t", max_loss_per_contract=80,
    )
    broker.submit_close(position, 0.10, "x")
    legs = broker._client.requests[0].legs
    assert all(leg.position_intent.value.endswith("to_close") for leg in legs)
    sides = {leg.symbol[-8:]: leg.side.value for leg in legs}
    assert sides["00598000"] == "buy"   # short put bought back
    assert sides["00597000"] == "sell"  # long put sold out


def test_net_price_is_reconstructed_from_the_legs():
    """Per-leg fills are unambiguous; the parent's average price is not."""
    def leg(price, side, ratio=1):
        return type("Leg", (), {
            "filled_avg_price": price,
            "side": type("S", (), {"value": side})(),
            "ratio_qty": ratio,
        })()

    order = type("Order", (), {
        "legs": [leg(0.30, "sell"), leg(0.10, "buy"), leg(0.25, "sell"), leg(0.08, "buy")],
        "filled_avg_price": None,
    })()
    assert _net_from_order(order) == pytest.approx(0.37)


def test_net_price_falls_back_to_the_parent_when_legs_are_unpriced():
    order = type("Order", (), {"legs": [], "filled_avg_price": 0.25})()
    assert _net_from_order(order) == pytest.approx(-0.25)


# -- simulator ---------------------------------------------------------------------


def test_sim_fills_at_the_conservative_credit_and_not_above(condor):
    sim = SimBroker()
    at_floor = sim.poll_order(sim.submit_open(condor, 1, condor.net_credit, "a"))
    above = sim.poll_order(sim.submit_open(condor, 1, condor.mid_credit, "b"))
    assert at_floor.is_filled
    assert not above.is_filled
