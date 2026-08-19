"""Position sizing from defined risk.

Size is derived from max loss, never from credit and never from a contract count the
model suggested. On a small account the honest answer is often zero contracts, and
zero is a valid size — it is not a bug to be worked around by widening the wings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import AgentConfig
from .models import Account, CondorQuote


@dataclass(frozen=True)
class SizeDecision:
    contracts: int
    risk_dollars: float
    reason: str

    @property
    def ok(self) -> bool:
        return self.contracts > 0


def size_condor(condor: CondorQuote, account: Account, config: AgentConfig) -> SizeDecision:
    risk = config.risk
    per_contract = condor.max_loss_per_contract
    if per_contract <= 0:
        return SizeDecision(0, 0.0, "structure has no defined loss; refusing to size it")

    budget = account.equity * risk.max_risk_pct
    by_budget = math.floor(budget / per_contract)

    available = (account.options_buying_power or account.buying_power) * (
        1.0 - risk.buying_power_headroom
    )
    by_buying_power = math.floor(available / per_contract)

    contracts = max(0, min(by_budget, by_buying_power, risk.max_contracts))

    if contracts == 0:
        binding = "buying power" if by_buying_power < by_budget else "risk budget"
        return SizeDecision(
            0,
            0.0,
            f"one contract risks {per_contract:.0f} but {binding} allows "
            f"{min(budget, available):.0f} — account is too small for a "
            f"{condor.width:.2f}-wide condor today",
        )

    binding = "max_contracts cap"
    if contracts == by_budget:
        binding = f"{risk.max_risk_pct:.1%} risk budget"
    elif contracts == by_buying_power:
        binding = "buying power headroom"
    return SizeDecision(
        contracts,
        contracts * per_contract,
        f"{contracts} contract(s), {contracts * per_contract:.0f} at risk "
        f"({contracts * per_contract / account.equity:.2%} of equity), bound by {binding}",
    )
