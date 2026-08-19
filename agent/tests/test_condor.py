import pytest

from trading_agent.condor import atm_iv, run_gates, select_condor
from trading_agent.marketdata import SyntheticChainSource
from trading_agent.models import Right
from tests.conftest import DAY


def test_selection_targets_the_configured_delta(chain, config, t):
    sel = select_condor(chain, config, t)
    assert sel.ok
    c = sel.condor
    assert abs(abs(c.short_put.delta) - config.structure.short_delta) < 0.06
    assert abs(abs(c.short_call.delta) - config.structure.short_delta) < 0.06


def test_selection_places_wings_at_the_configured_width(chain, config, t):
    c = select_condor(chain, config, t).condor
    assert c.put_width == pytest.approx(config.structure.wing_width)
    assert c.call_width == pytest.approx(config.structure.wing_width)
    assert c.long_put.strike < c.short_put.strike < c.spot < c.short_call.strike < c.long_call.strike


def test_selection_fails_when_the_wing_strike_does_not_exist(chain, config, t):
    """A wing that cannot be bought is not a defined-risk trade, so we do not size one."""
    wide = config.model_copy(
        update={"structure": config.structure.model_copy(update={"wing_width": 3.0})}
    )
    narrow_chain = chain.__class__(
        chain.underlying,
        chain.expiry,
        chain.spot,
        chain.as_of,
        tuple(q for q in chain.quotes if abs(q.strike - chain.spot) <= 2.0),
    )
    sel = select_condor(narrow_chain, wide, t)
    assert not sel.ok
    assert "wing missing" in sel.reason


def test_conservative_credit_is_never_above_mid(chain, config, t):
    c = select_condor(chain, config, t).condor
    assert c.net_credit <= c.mid_credit


def test_max_loss_is_width_less_credit(chain, config, t):
    c = select_condor(chain, config, t).condor
    assert c.max_loss_per_contract == pytest.approx((c.width - c.net_credit) * 100)


def test_breakevens_bracket_the_short_strikes(chain, config, t):
    c = select_condor(chain, config, t).condor
    low, high = c.breakevens
    assert low < c.short_put.strike
    assert high > c.short_call.strike


# -- gates -------------------------------------------------------------------------


def _gates(chain, config, t, realized=0.09, prev_close=600.0):
    c = select_condor(chain, config, t).condor
    return c, run_gates(c, config, t, atm_iv(chain, t, 0.04), prev_close, realized)


def test_baseline_chain_passes_every_gate(chain, config, t):
    _, report = _gates(chain, config, t)
    assert report.passed, report.reason


def test_credit_ratio_gate_rejects_a_thin_credit(chain, config, t):
    strict = config.model_copy(
        update={"gate": config.gate.model_copy(update={"min_credit_ratio": 0.40})}
    )
    _, report = _gates(chain, strict, t)
    assert not report.passed
    assert any(r.name == "credit_ratio" for r in report.failures)


def test_vrp_gate_rejects_when_implied_does_not_exceed_realized(chain, config, t):
    """The edge condition. Implied at or below realized means nothing to harvest."""
    _, report = _gates(chain, config, t, realized=0.13)
    assert not report.passed
    assert any(r.name == "variance_risk_premium" for r in report.failures)


def test_vrp_gate_fails_closed_without_a_realized_reading(chain, config, t):
    _, report = _gates(chain, config, t, realized=None)
    assert any(r.name == "variance_risk_premium" for r in report.failures)


def test_gap_gate_rejects_an_outsized_overnight_move(chain, config, t):
    _, report = _gates(chain, config, t, prev_close=560.0)
    assert any(r.name == "open_gap" for r in report.failures)


def test_iv_band_gate_rejects_a_panicking_market(config, now, expiry_at, t):
    chain = SyntheticChainSource(spot=600, iv=0.80, as_of=now, expiry_at=expiry_at).get_chain(
        "SPY", DAY
    )
    _, report = _gates(chain, config, t, realized=0.10)
    assert any(r.name == "iv_band" for r in report.failures)


def test_strike_buffer_gate_rejects_strikes_pulled_too_close(chain, config, t):
    close_in = config.model_copy(
        update={"structure": config.structure.model_copy(update={"short_delta": 0.30})}
    )
    _, report = _gates(chain, close_in, t)
    assert any(r.name == "strike_buffer" for r in report.failures)


def test_liquidity_gate_rejects_a_wide_leg(chain, config, t):
    """Widen one selected leg after selection, so the gate is what rejects it."""
    from trading_agent.models import CondorQuote, OptionQuote

    c = select_condor(chain, config, t).condor
    wide = OptionQuote(
        c.short_call.symbol, c.short_call.underlying, c.short_call.expiry,
        c.short_call.strike, c.short_call.right,
        bid=0.05, ask=1.20, bid_size=50, ask_size=50,
        delta=c.short_call.delta, iv=c.short_call.iv,
    )
    damaged = CondorQuote(c.short_put, c.long_put, wide, c.long_call, c.spot)
    report = run_gates(damaged, config, t, atm_iv(chain, t, 0.04), 600.0, 0.09)
    assert any(r.name == "leg_liquidity" for r in report.failures)


def test_liquidity_gate_rejects_a_chain_with_no_size(config, now, expiry_at, t):
    chain = SyntheticChainSource(
        spot=600, iv=0.13, as_of=now, expiry_at=expiry_at, size=1
    ).get_chain("SPY", DAY)
    _, report = _gates(chain, config, t)
    assert any(r.name == "leg_liquidity" for r in report.failures)


def test_every_gate_runs_even_after_one_fails(chain, config, t):
    """The journal is supposed to show the whole picture, not stop at the first no."""
    _, report = _gates(chain, config, t, realized=0.13, prev_close=560.0)
    assert len(report.failures) >= 2
    assert {r.name for r in report.results} == {
        "symmetric_wings", "positive_credit", "credit_ratio", "balanced_sides",
        "leg_liquidity", "iv_band", "strike_buffer", "variance_risk_premium", "open_gap",
    }


def test_effective_delta_falls_back_to_the_mid_price(chain, config, t):
    """A feed with no greeks and no IV must still produce a correctly-shaped condor."""
    stripped = chain.__class__(
        chain.underlying,
        chain.expiry,
        chain.spot,
        chain.as_of,
        tuple(
            q.__class__(
                q.symbol, q.underlying, q.expiry, q.strike, q.right,
                q.bid, q.ask, q.bid_size, q.ask_size, None, None,
            )
            for q in chain.quotes
        ),
    )
    with_greeks = select_condor(chain, config, t).condor
    without = select_condor(stripped, config, t).condor
    assert without.short_put.strike == with_greeks.short_put.strike
    assert without.short_call.strike == with_greeks.short_call.strike


def test_balanced_sides_gate_rejects_a_one_sided_condor(chain, config, t):
    """Skew a single leg's bid so all the credit comes from the call side."""
    from trading_agent.models import OptionQuote

    quotes = []
    for q in chain.quotes:
        if q.right is Right.PUT and q.strike < chain.spot:
            quotes.append(
                OptionQuote(q.symbol, q.underlying, q.expiry, q.strike, q.right,
                            0.01, 0.03, q.bid_size, q.ask_size, q.delta, q.iv)
            )
        else:
            quotes.append(q)
    lopsided = chain.__class__(
        chain.underlying, chain.expiry, chain.spot, chain.as_of, tuple(quotes)
    )
    _, report = _gates(lopsided, config, t)
    assert any(r.name == "balanced_sides" for r in report.failures)
