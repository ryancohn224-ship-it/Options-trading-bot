import math

import pytest

from trading_agent import blackscholes as bs
from trading_agent.models import Right

T = 6 / 24 / 365


def test_put_call_delta_parity():
    c = bs.delta(600, 605, T, 0.13, Right.CALL)
    p = bs.delta(600, 605, T, 0.13, Right.PUT)
    assert c - p == pytest.approx(1.0, abs=1e-9)


def test_implied_vol_round_trips():
    price = bs.price(600, 604, T, 0.17, Right.CALL)
    assert bs.implied_vol(price, 600, 604, T, Right.CALL) == pytest.approx(0.17, abs=1e-4)


def test_implied_vol_returns_none_below_intrinsic():
    """A quote inside intrinsic is a broken quote, not a low-vol quote."""
    assert bs.implied_vol(0.5, 600, 590, T, Right.CALL) is None


def test_delta_degenerates_at_expiry():
    assert bs.delta(600, 590, 0.0, 0.13, Right.CALL) == 1.0
    assert bs.delta(600, 610, 0.0, 0.13, Right.CALL) == 0.0
    assert bs.delta(600, 610, 0.0, 0.13, Right.PUT) == -1.0


def test_expected_move_scales_with_root_time():
    a = bs.expected_move(600, 0.13, T)
    b = bs.expected_move(600, 0.13, 4 * T)
    assert b == pytest.approx(2 * a, rel=1e-9)


def test_sixteen_delta_strike_sits_near_one_sigma():
    """The structure config and the strike-buffer gate must agree by construction."""
    em = bs.expected_move(600, 0.13, T)
    strike = 600 + em
    d = bs.delta(600, strike, T, 0.13, Right.CALL)
    assert 0.13 < d < 0.20
