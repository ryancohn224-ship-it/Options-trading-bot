"""Configuration: YAML file + environment. Every trade logs the hash of the config that produced it."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class DataCfg(BaseModel):
    warehouse_dir: Path = Path("warehouse")
    state_dir: Path = Path("state")
    feed: Literal["opra", "indicative"] = "indicative"
    stock_history_days: int = 800  # enough for HAR monthly lags + regime fit


class UniverseCfg(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "IWM"])
    beta: dict[str, float] = Field(default_factory=dict)  # beta to SPY; default 1.0
    min_underlying_price: float = 20.0
    min_open_interest: int = 500
    max_spread_pct_of_mid: float = 0.08  # per leg
    max_spread_abs: float = 0.15


class StructureCfg(BaseModel):
    short_delta: float = 0.16
    width_pct: float = 0.01  # spread width as % of spot; rounded to strike increment
    min_dte: int = 35
    max_dte: int = 55
    profit_take: float = 0.50  # close at 50% of max profit
    loss_stop_mult: float = 2.0  # close when loss reaches 2x credit
    exit_dte: int = 21
    min_credit_to_width: float = 0.10  # verticals: >=10% of width; condors naturally ~2x


class VolTargetCfg(BaseModel):
    enabled: bool = True
    reference_market_vol: float = (
        0.16  # market vol at which we run full size; scalar = ref / forecast
    )
    max_scalar: float = 1.5
    min_scalar: float = 0.0


class RiskCfg(BaseModel):
    max_loss_per_trade_pct: float = 0.015
    max_positions: int = 8
    max_per_underlying: int = 2
    max_bp_utilization: float = 0.40
    delta_band_per_1k: float = 0.30  # |net beta-weighted delta| per $1k equity
    vega_cap_per_1k: float = 2.0  # |net vega| per $1k equity  ($ per vol point)
    gamma_floor_per_1k: float = -0.05
    daily_loss_halt_pct: float = 0.03
    monthly_loss_halve_pct: float = 0.08
    max_drawdown_kill_pct: float = 0.20
    consecutive_loss_pause: int = 3
    enforce_day_trade_guard: bool = True
    max_day_trades_per_5d: int = 3


class ExecutionCfg(BaseModel):
    sim_fill_frac_into_spread: float = 0.40  # backtest pessimism
    walker_steps: int = 5
    walker_seconds_per_step: int = 120
    walker_max_frac_into_spread: float = 0.60
    avoid_first_minutes: int = 15
    avoid_last_minutes: int = 15


class AlphaCfg(BaseModel):
    min_expected_return: float = 0.02  # expected P&L / max loss, net of modeled costs
    vrp_min_edge_vol_pts: float = 0.02  # IV - forecast RV must exceed 2 vol points
    skew_rich_percentile: float = 0.70
    trend_fast: int = 50
    trend_slow: int = 200
    factor_weights: dict[str, float] = Field(
        default_factory=lambda: {"vrp": 1.0, "skew": 0.5, "trend": 1.0}
    )


class Cfg(BaseModel):
    mode: Literal["backtest", "paper", "live"] = "paper"
    data: DataCfg = DataCfg()
    universe: UniverseCfg = UniverseCfg()
    structure: StructureCfg = StructureCfg()
    voltarget: VolTargetCfg = VolTargetCfg()
    risk: RiskCfg = RiskCfg()
    execution: ExecutionCfg = ExecutionCfg()
    alpha: AlphaCfg = AlphaCfg()

    def hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()[:12]


def load_cfg(path: str | Path | None = None) -> Cfg:
    path = Path(path or os.environ.get("OTB_CONFIG", "config/default.yaml"))
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        return Cfg(**raw)
    return Cfg()


class Creds(BaseModel):
    api_key: str | None = None
    secret_key: str | None = None
    paper: bool = True

    @classmethod
    def from_env(cls) -> Creds:
        return cls(
            api_key=os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY"),
            secret_key=os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY"),
            paper=os.environ.get("APCA_PAPER", "true").lower() != "false",
        )

    @property
    def present(self) -> bool:
        return bool(self.api_key and self.secret_key)
