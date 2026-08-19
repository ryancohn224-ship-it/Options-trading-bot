from datetime import date

import pytest

from trading_agent.models import Right
from trading_agent.occ import build, parse


def test_round_trip():
    symbol = build("SPY", date(2026, 8, 18), Right.CALL, 604.0)
    assert symbol == "SPY260818C00604000"
    assert parse(symbol) == ("SPY", date(2026, 8, 18), Right.CALL, 604.0)


def test_parses_roots_of_any_length():
    """Parsing from the right means a 4-character root does not shift the strike."""
    assert parse("SPXW  260818P05900000")[3] == 5900.0
    assert parse("A     260818P00050000")[0] == "A"


def test_fractional_strikes_survive_the_thousandths_encoding():
    assert build("SPY", date(2026, 8, 18), Right.PUT, 599.5) == "SPY260818P00599500"
    assert parse("SPY260818P00599500")[3] == 599.5


def test_rejects_unrepresentable_strike():
    with pytest.raises(ValueError):
        build("SPY", date(2026, 8, 18), Right.PUT, 599.00005)
