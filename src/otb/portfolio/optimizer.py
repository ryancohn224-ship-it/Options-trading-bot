"""Greedy constrained selection: rank candidates by expected_return × confidence, add while all portfolio
constraints hold. Quantities are integers, so a greedy knapsack with feasibility checks is more robust than
a continuous QP rounded afterwards, and at retail size qty is almost always 1."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Cfg
from ..strategy.types import Candidate
from .greeks import PortfolioGreeks


@dataclass
class Selection:
    cand: Candidate
    qty: int
    reason: str = ""


def select(
    cands: list[Candidate],
    cfg: Cfg,
    equity: float,
    pg: PortfolioGreeks,
    scalar: float,
    spy_spot: float | None,
    delta_target_sign: int = 0,
    min_expected_return: float = 0.0,
) -> tuple[list[Selection], list[str]]:
    r = cfg.risk
    log: list[str] = []
    per_1k = equity / 1000.0
    delta_band = r.delta_band_per_1k * per_1k
    vega_cap = r.vega_cap_per_1k * per_1k * scalar
    gamma_floor = r.gamma_floor_per_1k * per_1k * scalar
    max_positions = (
        max(1, int(round(r.max_positions * min(scalar, 1.0)))) if scalar < 1 else r.max_positions
    )
    max_loss_budget = r.max_loss_per_trade_pct * equity  # per trade: unscaled (granularity)
    bp_cap = min(
        r.max_bp_utilization * equity, r.max_positions * max_loss_budget * scalar
    )  # aggregate: scaled
    if scalar <= 0.05:
        return [], ["vol-target scalar ~0: no new entries"]

    bw, vega, gamma, bp, n = pg.bw_delta, pg.vega, pg.gamma, pg.bp_used, pg.n_positions
    per_und = dict(pg.per_underlying)
    ranked = sorted(
        (c for c in cands if c.expected_return * c.confidence > min_expected_return),
        key=lambda c: c.expected_return * c.confidence,
        reverse=True,
    )
    out: list[Selection] = []
    seen_und_kind = set()
    for c in ranked:
        key = (c.underlying, c.kind)
        if key in seen_und_kind:
            continue
        if n >= max_positions:
            log.append("max_positions")
            break
        if per_und.get(c.underlying, 0) >= r.max_per_underlying:
            continue
        qty = int(max_loss_budget // max(c.max_loss, 1e-6))
        if qty < 1:
            log.append(
                f"{c.underlying} {c.kind}: max_loss {c.max_loss:.0f} > budget {max_loss_budget:.0f}"
            )
            continue
        qty = min(qty, 10)
        spot_ratio = (c.spot / spy_spot) if spy_spot else 1.0
        beta = cfg.universe.beta.get(c.underlying, 1.0)
        bw_new = bw + c.net_delta * qty * beta * spot_ratio
        if abs(bw_new) > delta_band and abs(bw_new) > abs(bw):
            # try to respect the trend-driven sign: allow if it moves delta toward target sign
            if not (
                delta_target_sign != 0
                and (bw_new - bw) * delta_target_sign > 0
                and abs(bw_new) <= 1.5 * delta_band
            ):
                log.append(f"{c.underlying} {c.kind}: delta band")
                continue
        if abs(vega + c.net_vega * qty) > vega_cap:
            log.append(f"{c.underlying} {c.kind}: vega cap")
            continue
        if gamma + c.net_gamma * qty < gamma_floor:
            log.append(f"{c.underlying} {c.kind}: gamma floor")
            continue
        if bp + c.max_loss * qty > bp_cap:
            log.append(f"{c.underlying} {c.kind}: buying power")
            continue
        out.append(Selection(c, qty, "ok"))
        seen_und_kind.add(key)
        bw, vega, gamma, bp, n = (
            bw_new,
            vega + c.net_vega * qty,
            gamma + c.net_gamma * qty,
            bp + c.max_loss * qty,
            n + 1,
        )
        per_und[c.underlying] = per_und.get(c.underlying, 0) + 1
    return out, log
