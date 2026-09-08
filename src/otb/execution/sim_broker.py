"""Simulated broker with a deliberately pessimistic fill model:
each leg fills `frac` of the half-spread away from mid, on the unfavorable side. Expiring positions settle at intrinsic."""

from __future__ import annotations

import uuid
from datetime import date

import polars as pl

from ..config import Cfg
from ..strategy.types import Candidate, Position
from .broker import AccountSnapshot, Fill


class SimBroker:
    def __init__(self, cfg: Cfg, starting_cash: float):
        self.cfg = cfg
        self.cash = float(starting_cash)
        self.positions: list[Position] = []
        self.frac = cfg.execution.sim_fill_frac_into_spread
        self.fills: list[dict] = []

    # ----- marks -----
    @staticmethod
    def _quotes(chain: pl.DataFrame) -> dict:
        q = chain.select(["symbol", "bid", "ask", "mid", "spot", "strike", "is_call"]).to_dict(
            as_series=False
        )
        return {
            s: (
                q["bid"][i],
                q["ask"][i],
                q["mid"][i],
                q["spot"][i],
                q["strike"][i],
                q["is_call"][i],
            )
            for i, s in enumerate(q["symbol"])
        }

    def _close_debit(
        self, p: Position, quotes: dict, fill: bool, spot_fallback: float | None, today: date
    ) -> float | None:
        total = 0.0
        for leg in p.legs:
            q = quotes.get(leg["symbol"])
            if q is None:
                if today >= p.expiry and spot_fallback is not None:
                    intrinsic = (
                        max(0.0, spot_fallback - leg["strike"])
                        if leg["is_call"]
                        else max(0.0, leg["strike"] - spot_fallback)
                    )
                    total += -leg["side"] * intrinsic
                    continue
                return None
            bid, ask, mid = q[0] or 0.0, q[1] or 0.0, q[2] or 0.0
            half = max(ask - bid, 0.0) / 2
            if leg["side"] < 0:  # short leg: we buy back → pay mid + frac*half
                total += mid + (self.frac * half if fill else 0.0)
            else:  # long leg: we sell → receive mid - frac*half
                total -= mid - (self.frac * half if fill else 0.0)
        return total

    def account(self, chain: pl.DataFrame | None = None) -> AccountSnapshot:
        liab, bp_used = 0.0, 0.0
        if chain is not None and not chain.is_empty():
            quotes = self._quotes(chain)
            for p in self.positions:
                if p.status != "open":
                    continue
                spot = None
                for leg in p.legs:
                    q = quotes.get(leg["symbol"])
                    if q:
                        spot = q[3]
                        break
                d = self._close_debit(p, quotes, fill=False, spot_fallback=spot, today=date.max)
                if d is not None:
                    liab += d * 100 * p.qty
                bp_used += p.max_loss * p.qty
        eq = self.cash - liab
        return AccountSnapshot(equity=eq, cash=self.cash, buying_power=max(eq - bp_used, 0.0))

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status == "open"]

    def open(
        self,
        cand: Candidate,
        qty: int,
        today: date,
        config_hash: str,
        beta: float,
        chain: pl.DataFrame | None = None,
    ):
        credit = 0.0
        for l in cand.legs:
            half = l.spread / 2
            credit += (l.mid - self.frac * half) if l.side < 0 else -(l.mid + self.frac * half)
        if credit <= 0:
            return Fill(False, 0.0, cand.credit_mid, "non-positive credit after slippage"), None
        pos = Position(
            id=uuid.uuid4().hex[:10],
            underlying=cand.underlying,
            kind=cand.kind,
            expiry=cand.expiry,
            legs=[
                {"symbol": l.symbol, "side": l.side, "strike": l.strike, "is_call": l.is_call}
                for l in cand.legs
            ],
            qty=qty,
            open_date=today,
            credit_received=credit,
            entry_mid=cand.credit_mid,
            max_loss=(cand.width - credit) * 100,
            width=cand.width,
            config_hash=config_hash,
            beta=beta,
            meta={"expected_return": cand.expected_return, "scores": cand.scores},
        )
        self.positions.append(pos)
        self.cash += credit * 100 * qty
        self.fills.append(
            {
                "date": today.isoformat(),
                "pos": pos.id,
                "action": "open",
                "mid": cand.credit_mid,
                "fill": credit,
                "qty": qty,
            }
        )
        return Fill(True, credit, cand.credit_mid, "sim"), pos

    def close(
        self, pos: Position, today: date, reason: str, chain: pl.DataFrame | None = None
    ) -> Fill:
        quotes = self._quotes(chain) if chain is not None else {}
        spot = None
        for leg in pos.legs:
            q = quotes.get(leg["symbol"])
            if q:
                spot = q[3]
                break
        if spot is None and chain is not None and not chain.is_empty():
            u = chain.filter(pl.col("underlying") == pos.underlying)
            spot = float(u["spot"][0]) if not u.is_empty() else None
        debit = self._close_debit(pos, quotes, fill=True, spot_fallback=spot, today=today)
        if debit is None:
            return Fill(False, 0.0, 0.0, "no quotes to close")
        mid = self._close_debit(pos, quotes, fill=False, spot_fallback=spot, today=today) or debit
        pnl = (pos.credit_received - debit) * 100 * pos.qty
        pnl = max(
            pnl, -pos.max_loss * pos.qty
        )  # defined risk: cannot lose more than width - credit
        self.cash -= debit * 100 * pos.qty
        pos.status, pos.close_date, pos.close_debit, pos.realized_pnl, pos.close_reason = (
            "closed",
            today,
            debit,
            pnl,
            reason,
        )
        self.fills.append(
            {
                "date": today.isoformat(),
                "pos": pos.id,
                "action": "close",
                "mid": mid,
                "fill": debit,
                "qty": pos.qty,
                "reason": reason,
            }
        )
        return Fill(True, debit, mid, "sim")
