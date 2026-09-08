"""Configuration: YAML in, validated Pydantic models out, hash logged with every trade.

Nothing about the strategy is hardcoded, but the *bounds* are. A config file cannot
ask this agent to sell a 45-delta strike or risk 30% of the account on one condor —
those limits live in the field constraints below, in code, under version control.
That is deliberate: guardrails enforced only in a prompt or only in a YAML file are
guardrails that get loosened at 3pm on a bad day.
"""

from __future__ import annotations

import hashlib
import json
from datetime import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketConfig(StrictModel):
    underlying: str = "SPY"
    timezone: str = "America/New_York"
    #: 0DTE only. Kept configurable so the same code can be pointed at 1DTE in research.
    dte: int = Field(default=0, ge=0, le=2)
    risk_free_rate: float = Field(default=0.04, ge=0.0, le=0.20)
    #: SPY options are PM-settled; the contract stops trading at the close.
    expiry_time: time = time(16, 0)


class ScheduleConfig(StrictModel):
    """All times are exchange-local (MarketConfig.timezone), never UTC, never server-local."""

    #: Do not touch the open. The first half hour prices the overnight gap, not the day.
    entry_start: time = time(10, 0)
    #: After this, there is not enough remaining theta to pay for the tail risk.
    entry_cutoff: time = time(12, 30)
    #: Management polls between entry and the hard flat time.
    manage_interval_seconds: int = Field(default=300, ge=30, le=3600)
    #: Hard flat. Never carry a 0DTE short into settlement.
    force_flat: time = time(15, 45)

    @model_validator(mode="after")
    def _ordered(self) -> "ScheduleConfig":
        if not (self.entry_start < self.entry_cutoff < self.force_flat):
            raise ValueError("require entry_start < entry_cutoff < force_flat")
        return self


class StructureConfig(StrictModel):
    """Shape of the condor before any pricing gate runs."""

    #: |delta| of the short strikes. 0.16 is roughly the 1-sigma wing.
    short_delta: float = Field(default=0.16, gt=0.02, le=0.30)
    #: Dollar distance from short to long strike, per side.
    wing_width: float = Field(default=1.0, gt=0.0, le=25.0)
    #: Strikes must exist on both sides at this width or we do not trade.
    strike_increment: float = Field(default=1.0, gt=0.0)


class GateConfig(StrictModel):
    """The credit gate. This is the strategy's actual edge condition.

    An iron condor is only worth doing when the premium compensates for the width
    being risked. If the market will not pay, the correct trade is no trade — a
    condor entered for 8% of width has to win ~92% of the time to break even, and
    0DTE SPY does not win 92% of the time.
    """

    #: net_credit / wing_width. A reward-to-risk floor, NOT an edge claim — see the
    #: VRP gate below for the actual edge condition, and docs/AGENT.md for why the
    #: distinction matters.
    min_credit_ratio: float = Field(default=0.12, gt=0.0, lt=1.0)
    #: Each side must carry its share, or it is a directional spread in a condor costume.
    min_side_credit_ratio: float = Field(default=0.06, ge=0.0, lt=0.5)
    #: Reject illiquid legs: absolute and relative top-of-book spread caps.
    max_leg_spread_abs: float = Field(default=0.10, gt=0.0)
    max_leg_spread_pct: float = Field(default=0.60, gt=0.0, le=2.0)
    #: Minimum displayed size on every leg we need to trade against.
    min_leg_size: int = Field(default=10, ge=0)
    #: Short strikes must sit at least this many expected moves from spot. A 0.16-delta
    #: strike is ~1 sigma out by construction, so this is a backstop against a skewed
    #: or stale surface pulling a strike in, not the primary selector.
    min_strike_buffer_sigma: float = Field(default=0.85, ge=0.0, le=4.0)
    #: Stand down after an outsized overnight gap; the day's distribution is not the usual one.
    max_open_gap_pct: float = Field(default=0.015, gt=0.0, le=0.10)
    #: Below this VIX-equivalent there is no premium worth selling; above it, the
    #: tail is not something a defined-risk condor should be short into.
    min_atm_iv: float = Field(default=0.06, ge=0.0)
    max_atm_iv: float = Field(default=0.45, gt=0.0)

    #: The edge condition. Implied vol must exceed a recent realized-vol estimate by
    #: this fraction before we sell premium. A short condor is risk-neutral-fair by
    #: construction; the only structural reason to be short it is that implied has
    #: historically printed above subsequently realized. If today is not one of those
    #: days, there is nothing here to harvest.
    min_vrp: float = Field(default=0.15, ge=0.0, le=2.0)
    #: Trading days of close-to-close history behind the realized-vol estimate.
    realized_vol_lookback: int = Field(default=20, ge=5, le=120)

    @model_validator(mode="after")
    def _iv_band(self) -> "GateConfig":
        if self.min_atm_iv >= self.max_atm_iv:
            raise ValueError("min_atm_iv must be below max_atm_iv")
        return self


class RiskConfig(StrictModel):
    #: Defined risk per condor as a fraction of equity.
    max_risk_pct: float = Field(default=0.02, gt=0.0, le=0.05)
    #: Absolute cap regardless of what the percentage allows.
    max_contracts: int = Field(default=10, ge=1, le=100)
    #: One structure per session. Averaging into a losing 0DTE condor is not a strategy.
    max_entries_per_day: int = Field(default=1, ge=1, le=3)
    #: Below this the position sizing arithmetic stops producing sane structures.
    min_equity: float = Field(default=5_000.0, ge=2_000.0)
    #: Trip the kill switch for the day at this realized loss.
    max_daily_loss_pct: float = Field(default=0.03, gt=0.0, le=0.10)
    #: Require a human to clear the kill switch after this many losing days in a row.
    max_consecutive_loss_days: int = Field(default=3, ge=1, le=20)
    #: Keep this fraction of buying power unused as headroom.
    buying_power_headroom: float = Field(default=0.5, ge=0.0, lt=1.0)


class ExitConfig(StrictModel):
    """Both thresholds are compared against the *mid* mark. See `exits.py` for why."""

    #: Close when the structure's mid value falls to (1 - target) x credit.
    profit_target: float = Field(default=0.50, gt=0.0, lt=1.0)
    #: Close when unrealised loss reaches this fraction of the defined max loss.
    #: Expressed against max loss rather than against the credit, because max loss is
    #: a fixed known number and the credit is a spread-crossed one.
    stop_loss_fraction: float = Field(default=0.60, gt=0.0, le=1.0)


class ExecutionConfig(StrictModel):
    """Limit ladder. We start at the price the gate approved and walk toward mid.

    We never walk *below* the gate credit — if the market will not pay the gated
    price, the trade did not qualify in the first place.
    """

    ladder_steps: int = Field(default=4, ge=1, le=10)
    ladder_step_seconds: int = Field(default=20, ge=1, le=120)
    #: How far down the mid-to-gate distance the final step is allowed to reach.
    #: 1.0 walks all the way to the gated credit — which is the price the gate
    #: actually approved, so it is the default. Lower values only make the agent
    #: fussier than its own gate; nothing above 1.0 is representable, by design.
    ladder_max_concession: float = Field(default=1.0, ge=0.0, le=1.0)
    #: Exits are urgent; give up this fraction of the mid to get flat.
    exit_slippage_allowance: float = Field(default=0.10, ge=0.0, le=1.0)


class CostConfig(StrictModel):
    """Pass-through fees, which a naive backtest ignores and which decide marginal trades.

    Alpaca charges no commission on options, but OCC clearing, the options regulatory
    fee and exchange fees are passed through at roughly six cents per contract per
    transaction. A condor is four legs in and four legs out: eight transactions, about
    fifty cents per contract round trip. Against a thirteen-cent credit — thirteen
    dollars per contract — that is four percent of the gross, and it is the difference
    between a marginal strategy and a losing one.
    """

    per_contract_leg: float = Field(default=0.065, ge=0.0, le=2.0)
    #: Legs charged when the structure is closed by order rather than left to expire.
    legs_round_trip: int = Field(default=8, ge=1, le=8)
    #: Legs charged when it is allowed to settle (entry only; worthless legs cost nothing).
    legs_to_expiry: int = Field(default=4, ge=1, le=8)


class LLMConfig(StrictModel):
    """The model's authority, stated precisely.

    It may veto a trade the deterministic gates approved, and it writes the journal
    narrative. It may not choose strikes, set size, loosen a gate, or place an order.
    A veto is safe in one direction only, so that is the only direction it gets.
    """

    enabled: bool = False
    command: list[str] = Field(default_factory=lambda: ["claude", "-p"])
    timeout_seconds: int = Field(default=120, ge=10, le=600)
    #: If the model errors or returns junk, trade anyway (the Python gates are the
    #: real control) but say so in the journal.
    fail_open: bool = True


class PathsConfig(StrictModel):
    state_dir: Path = Path("var/state")
    journal_dir: Path = Path("var/journal")
    kill_switch_file: Path = Path("var/state/KILL_SWITCH")
    #: Stored chains. The research asset — see snapshot.py.
    snapshot_dir: Path = Path("var/snapshots")
    #: Proposals, promotions, holdout openings, retirements.
    research_dir: Path = Path("var/research")
    report_dir: Path = Path("var/reports")
    variants_file: Path = Path("config/variants.yaml")


class AgentConfig(StrictModel):
    mode: Literal["paper", "live"] = "paper"
    market: MarketConfig = MarketConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    structure: StructureConfig = StructureConfig()
    gate: GateConfig = GateConfig()
    risk: RiskConfig = RiskConfig()
    exits: ExitConfig = ExitConfig()
    execution: ExecutionConfig = ExecutionConfig()
    costs: CostConfig = CostConfig()
    llm: LLMConfig = LLMConfig()
    paths: PathsConfig = PathsConfig()

    @model_validator(mode="after")
    def _wing_covers_increment(self) -> "AgentConfig":
        ratio = self.structure.wing_width / self.structure.strike_increment
        if abs(ratio - round(ratio)) > 1e-9:
            raise ValueError("wing_width must be a whole multiple of strike_increment")
        return self

    @property
    def hash(self) -> str:
        """Short digest of the effective config, stamped on every journal entry."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


def load_config(path: str | Path | None = None) -> AgentConfig:
    if path is None:
        return AgentConfig()
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return AgentConfig.model_validate(raw)
