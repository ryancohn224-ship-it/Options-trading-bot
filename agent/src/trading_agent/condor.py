"""Strike selection and the credit gate.

Two jobs, deliberately separated:

1. `select_condor` decides *which* structure the shape config asks for. It knows about
   deltas and strike increments. It does not know whether the trade is worth doing.
2. `run_gates` decides *whether* to do it. It knows about prices, liquidity and the
   day's context. It cannot move a strike to make a trade qualify.

Keeping them apart is what stops the classic drift where a failing gate quietly
becomes a nearer strike until something passes.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import blackscholes as bs
from .config import AgentConfig
from .models import CondorQuote, GateReport, GateResult, OptionChain, OptionQuote, Right


def effective_delta(
    quote: OptionQuote, spot: float, t: float, rate: float
) -> float | None:
    """Feed delta if present, else from feed IV, else from the mid price.

    Returns None only when the contract has no usable price at all, which is itself
    a signal: a leg with no market is a leg we cannot trade.
    """
    if quote.delta is not None:
        return quote.delta
    if quote.iv is not None and quote.iv > 0:
        return bs.delta(spot, quote.strike, t, quote.iv, quote.right, rate)
    if quote.mid > 0:
        iv = bs.implied_vol(quote.mid, spot, quote.strike, t, quote.right, rate)
        if iv:
            return bs.delta(spot, quote.strike, t, iv, quote.right, rate)
    return None


def atm_iv(chain: OptionChain, t: float, rate: float) -> float | None:
    """Average the two nearest-the-money contracts' implied vols.

    Used as the day's volatility reading: it sets the expected move that the strike
    buffer gate measures against, and it is the input to the IV band gate.
    """
    vols: list[float] = []
    for right in (Right.CALL, Right.PUT):
        side = chain.side(right)
        if not side:
            continue
        nearest = min(side, key=lambda q: abs(q.strike - chain.spot))
        if nearest.iv and nearest.iv > 0:
            vols.append(nearest.iv)
        elif nearest.mid > 0:
            iv = bs.implied_vol(nearest.mid, chain.spot, nearest.strike, t, right, rate)
            if iv:
                vols.append(iv)
    return sum(vols) / len(vols) if vols else None


@dataclass(frozen=True)
class Selection:
    condor: CondorQuote | None
    reason: str

    @property
    def ok(self) -> bool:
        return self.condor is not None


def _pick_short(
    candidates: list[OptionQuote], target: float, spot: float, t: float, rate: float
) -> OptionQuote | None:
    """The tradeable contract whose |delta| sits closest to the target."""
    best: tuple[float, OptionQuote] | None = None
    for q in candidates:
        if not q.has_two_sided_market:
            continue
        d = effective_delta(q, spot, t, rate)
        if d is None:
            continue
        distance = abs(abs(d) - target)
        if best is None or distance < best[0]:
            best = (distance, q)
    return best[1] if best else None


def select_condor(chain: OptionChain, config: AgentConfig, t: float) -> Selection:
    cfg = config.structure
    rate = config.market.risk_free_rate
    spot = chain.spot

    otm_puts = [q for q in chain.side(Right.PUT) if q.strike < spot]
    otm_calls = [q for q in chain.side(Right.CALL) if q.strike > spot]
    if not otm_puts or not otm_calls:
        return Selection(None, "chain has no out-of-the-money strikes on one or both sides")

    short_put = _pick_short(otm_puts, cfg.short_delta, spot, t, rate)
    short_call = _pick_short(otm_calls, cfg.short_delta, spot, t, rate)
    if short_put is None or short_call is None:
        return Selection(None, "no tradeable strike with a resolvable delta near the target")

    long_put = chain.at_strike(Right.PUT, short_put.strike - cfg.wing_width)
    long_call = chain.at_strike(Right.CALL, short_call.strike + cfg.wing_width)
    if long_put is None or long_call is None:
        return Selection(
            None,
            f"protective wing missing at width {cfg.wing_width} "
            f"(need {short_put.strike - cfg.wing_width}P and {short_call.strike + cfg.wing_width}C)",
        )
    if short_put.strike >= short_call.strike:
        return Selection(None, "short strikes crossed; chain or spot is inconsistent")

    return Selection(
        CondorQuote(
            short_put=short_put,
            long_put=long_put,
            short_call=short_call,
            long_call=long_call,
            spot=spot,
        ),
        "selected",
    )


def _leg_liquidity_gate(condor: CondorQuote, config: AgentConfig) -> GateResult:
    cfg = config.gate
    problems: list[str] = []
    for leg in condor.legs:
        q = leg.quote
        if not q.has_two_sided_market:
            problems.append(f"{q.symbol} has no two-sided market ({q.bid}/{q.ask})")
            continue
        if q.spread > cfg.max_leg_spread_abs and q.spread_pct > cfg.max_leg_spread_pct:
            problems.append(
                f"{q.symbol} spread {q.spread:.2f} ({q.spread_pct:.0%} of mid)"
            )
        # We only need size on the side we actually trade against.
        size = q.bid_size if leg.is_short else q.ask_size
        if cfg.min_leg_size and size < cfg.min_leg_size:
            problems.append(f"{q.symbol} shows only {size} up")
    return GateResult(
        "leg_liquidity",
        not problems,
        "; ".join(problems) if problems else "all four legs quoted and sized",
    )


def run_gates(
    condor: CondorQuote,
    config: AgentConfig,
    t: float,
    iv: float | None,
    prev_close: float | None,
    realized_vol: float | None = None,
) -> GateReport:
    """Every gate runs even after one fails, so the journal records the whole picture."""
    cfg = config.gate
    width = condor.width
    results: list[GateResult] = []

    results.append(
        GateResult(
            "symmetric_wings",
            abs(condor.put_width - condor.call_width) < 1e-9,
            f"put wing {condor.put_width}, call wing {condor.call_width}",
        )
    )

    results.append(
        GateResult(
            "positive_credit",
            condor.net_credit > 0,
            f"net credit {condor.net_credit:.3f} at conservative prices",
        )
    )

    results.append(
        GateResult(
            "credit_ratio",
            condor.credit_ratio >= cfg.min_credit_ratio,
            f"{condor.credit_ratio:.1%} of {width:.2f} width "
            f"(floor {cfg.min_credit_ratio:.0%}, "
            f"risking {condor.max_loss_per_contract:.0f} to make {condor.net_credit * 100:.0f})",
        )
    )

    put_ratio = condor.put_credit / width if width else 0.0
    call_ratio = condor.call_credit / width if width else 0.0
    results.append(
        GateResult(
            "balanced_sides",
            min(put_ratio, call_ratio) >= cfg.min_side_credit_ratio,
            f"put side {put_ratio:.1%}, call side {call_ratio:.1%} "
            f"(each must clear {cfg.min_side_credit_ratio:.0%})",
        )
    )

    results.append(_leg_liquidity_gate(condor, config))

    if iv is None:
        results.append(GateResult("iv_band", False, "no at-the-money implied vol available"))
        results.append(
            GateResult("strike_buffer", False, "cannot size the expected move without an IV")
        )
    else:
        results.append(
            GateResult(
                "iv_band",
                cfg.min_atm_iv <= iv <= cfg.max_atm_iv,
                f"ATM IV {iv:.1%} against band {cfg.min_atm_iv:.0%}-{cfg.max_atm_iv:.0%}",
            )
        )
        em = bs.expected_move(condor.spot, iv, t)
        put_buffer = (condor.spot - condor.short_put.strike) / em if em > 0 else 0.0
        call_buffer = (condor.short_call.strike - condor.spot) / em if em > 0 else 0.0
        results.append(
            GateResult(
                "strike_buffer",
                min(put_buffer, call_buffer) >= cfg.min_strike_buffer_sigma,
                f"shorts sit {put_buffer:.2f}σ / {call_buffer:.2f}σ out on a "
                f"{em:.2f} expected move (floor {cfg.min_strike_buffer_sigma:.2f}σ)",
            )
        )

    # The edge condition. Everything above is risk control; this is the only gate that
    # claims a reason to be short premium at all.
    if iv is None or realized_vol is None or realized_vol <= 0:
        results.append(
            GateResult(
                "variance_risk_premium",
                False,
                "cannot compare implied against realized without both readings",
            )
        )
    else:
        vrp = iv / realized_vol - 1.0
        results.append(
            GateResult(
                "variance_risk_premium",
                vrp >= cfg.min_vrp,
                f"implied {iv:.1%} against {cfg.realized_vol_lookback}-day realized "
                f"{realized_vol:.1%} = {vrp:+.0%} premium (floor {cfg.min_vrp:+.0%})",
            )
        )

    if prev_close is None or prev_close <= 0:
        results.append(GateResult("open_gap", True, "no prior close available; gap check skipped"))
    else:
        gap = abs(condor.spot / prev_close - 1.0)
        results.append(
            GateResult(
                "open_gap",
                gap <= cfg.max_open_gap_pct,
                f"{gap:.2%} from prior close {prev_close:.2f} (cap {cfg.max_open_gap_pct:.2%})",
            )
        )

    return GateReport(tuple(results))
