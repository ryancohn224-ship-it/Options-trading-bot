import pytest

from trading_agent.condor import select_condor
from trading_agent.models import Account
from trading_agent.sizing import size_condor


@pytest.fixture
def condor(chain, config, t):
    return select_condor(chain, config, t).condor


def test_risk_budget_binds_at_a_normal_account(condor, config):
    account = Account(equity=25_000, buying_power=50_000)
    decision = size_condor(condor, account, config)
    assert decision.ok
    assert decision.risk_dollars <= account.equity * config.risk.max_risk_pct


def test_size_is_zero_when_one_contract_exceeds_the_budget(condor, config):
    """A $1,000 account cannot risk 2% on a structure whose minimum risk is ~$87."""
    decision = size_condor(condor, Account(equity=1_000, buying_power=2_000), config)
    assert not decision.ok
    assert decision.contracts == 0
    assert "too small" in decision.reason


def test_buying_power_headroom_can_bind_before_the_risk_budget(condor, config):
    decision = size_condor(condor, Account(equity=100_000, buying_power=1_000), config)
    assert decision.contracts <= 5
    assert "buying power" in decision.reason


def test_absolute_contract_cap_is_never_exceeded(condor, config):
    decision = size_condor(condor, Account(equity=10_000_000, buying_power=10_000_000), config)
    assert decision.contracts == config.risk.max_contracts


def test_options_buying_power_wins_over_general_buying_power(condor, config):
    """Equity and cash buying power both allow the cap; options buying power does not."""
    generous = Account(equity=100_000, buying_power=500_000)
    constrained = Account(equity=100_000, buying_power=500_000, options_buying_power=500)
    assert size_condor(condor, generous, config).contracts == config.risk.max_contracts
    assert size_condor(condor, constrained, config).contracts == 2
