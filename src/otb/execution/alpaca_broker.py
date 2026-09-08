"""Alpaca broker: multi-leg limit orders with a limit walker, position ledger persisted locally, reconciliation."""

from __future__ import annotations

import json
import time
import uuid
from datetime import date
from pathlib import Path

import polars as pl

from ..config import Cfg, Creds
from ..strategy.types import Candidate, Position
from .broker import AccountSnapshot, Fill


class AlpacaBroker:
    def __init__(self, cfg: Cfg, creds: Creds, ledger_path: Path, dry_run: bool = False):
        from alpaca.trading.client import TradingClient

        self.cfg = cfg
        self.tc = TradingClient(creds.api_key, creds.secret_key, paper=creds.paper)
        self.ledger_path = ledger_path
        self.dry_run = dry_run
        self.positions: list[Position] = self._load()

    # ----- ledger -----
    def _load(self) -> list[Position]:
        if self.ledger_path.exists():
            return [Position.from_dict(d) for d in json.loads(self.ledger_path.read_text())]
        return []

    def _save(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(json.dumps([p.to_dict() for p in self.positions], indent=2))

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status == "open"]

    # ----- account -----
    def account(self, chain: pl.DataFrame | None = None) -> AccountSnapshot:
        a = self.tc.get_account()
        return AccountSnapshot(
            equity=float(a.equity), cash=float(a.cash), buying_power=float(a.buying_power)
        )

    def clock(self):
        return self.tc.get_clock()

    def broker_option_positions(self) -> dict[str, int]:
        out = {}
        for p in self.tc.get_all_positions():
            if (
                getattr(p, "asset_class", None)
                and str(p.asset_class).endswith("option")
                or len(p.symbol) > 12
            ):
                out[p.symbol] = int(float(p.qty))
        return out

    def reconcile(self) -> list[str]:
        """Compare ledger legs vs broker positions. Returns discrepancies (empty = consistent)."""
        expected: dict[str, int] = {}
        for p in self.open_positions():
            for leg in p.legs:
                expected[leg["symbol"]] = expected.get(leg["symbol"], 0) + leg["side"] * p.qty
        actual = self.broker_option_positions()
        issues = []
        for s in set(expected) | set(actual):
            if expected.get(s, 0) != actual.get(s, 0):
                issues.append(f"{s}: ledger {expected.get(s, 0)} vs broker {actual.get(s, 0)}")
        return issues

    # ----- orders -----
    def _legs(self, legs: list[dict], opening: bool):
        from alpaca.trading.enums import OrderSide, PositionIntent
        from alpaca.trading.requests import OptionLegRequest

        out = []
        for l in legs:
            short = l["side"] < 0
            if opening:
                side = OrderSide.SELL if short else OrderSide.BUY
                intent = PositionIntent.SELL_TO_OPEN if short else PositionIntent.BUY_TO_OPEN
            else:
                side = OrderSide.BUY if short else OrderSide.SELL
                intent = PositionIntent.BUY_TO_CLOSE if short else PositionIntent.SELL_TO_CLOSE
            out.append(
                OptionLegRequest(symbol=l["symbol"], ratio_qty=1, side=side, position_intent=intent)
            )
        return out

    def _walk(
        self, legs, qty: int, start_net: float, worst_net: float, client_id: str
    ) -> tuple[bool, float, str]:
        """Submit an mleg limit at start_net and step toward worst_net. Net price sign: negative = credit."""
        from alpaca.trading.enums import OrderClass, OrderType, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, ReplaceOrderRequest

        ex = self.cfg.execution
        steps = max(ex.walker_steps, 1)
        prices = [
            start_net + (worst_net - start_net) * i / (steps - 1) if steps > 1 else start_net
            for i in range(steps)
        ]
        if self.dry_run:
            return True, prices[0], "dry-run"
        req = LimitOrderRequest(
            qty=qty,
            order_class=OrderClass.MLEG,
            type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            legs=legs,
            limit_price=round(prices[0], 2),
            client_order_id=client_id,
        )
        order = self.tc.submit_order(req)
        oid = order.id
        for i, px in enumerate(prices):
            if i > 0:
                try:
                    order = self.tc.replace_order_by_id(
                        oid, ReplaceOrderRequest(limit_price=round(px, 2))
                    )
                    oid = order.id
                except Exception as e:  # already filled or cannot replace
                    o = self.tc.get_order_by_id(oid)
                    if str(o.status).endswith("filled"):
                        return True, float(o.filled_avg_price or px), "filled"
                    return False, 0.0, f"replace failed: {e}"
            deadline = time.time() + ex.walker_seconds_per_step
            while time.time() < deadline:
                o = self.tc.get_order_by_id(oid)
                st = str(o.status).lower()
                if st.endswith("filled") and not st.endswith("partially_filled"):
                    return True, float(o.filled_avg_price or px), "filled"
                if st in ("canceled", "rejected", "expired"):
                    return False, 0.0, st
                time.sleep(5)
        try:
            self.tc.cancel_order_by_id(oid)
        except Exception:
            pass
        return False, 0.0, "unfilled after walk"

    def open(
        self,
        cand: Candidate,
        qty: int,
        today: date,
        config_hash: str,
        beta: float,
        chain: pl.DataFrame | None = None,
    ):
        ex = self.cfg.execution
        half = sum(l.spread for l in cand.legs) / 2
        start = -cand.credit_mid
        worst = -(cand.credit_mid - ex.walker_max_frac_into_spread * half)
        ok, net, detail = self._walk(
            self._legs([{"symbol": l.symbol, "side": l.side} for l in cand.legs], True),
            qty,
            start,
            worst,
            client_id=f"otb-o-{uuid.uuid4().hex[:8]}",
        )
        if not ok:
            return Fill(False, 0.0, cand.credit_mid, detail), None
        credit = -net if net < 0 else abs(net)
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
            meta={"expected_return": cand.expected_return, "scores": cand.scores, "detail": detail},
        )
        self.positions.append(pos)
        self._save()
        return Fill(True, credit, cand.credit_mid, detail), pos

    def close(
        self, pos: Position, today: date, reason: str, chain: pl.DataFrame | None = None
    ) -> Fill:
        ex = self.cfg.execution
        mid, half = 0.0, 0.0
        if chain is not None and not chain.is_empty():
            q = chain.select(["symbol", "bid", "ask", "mid"]).to_dict(as_series=False)
            idx = {s: i for i, s in enumerate(q["symbol"])}
            for leg in pos.legs:
                i = idx.get(leg["symbol"])
                if i is None:
                    continue
                mid += -leg["side"] * (q["mid"][i] or 0.0)
                half += max((q["ask"][i] or 0) - (q["bid"][i] or 0), 0) / 2
        start, worst = mid, mid + ex.walker_max_frac_into_spread * half
        ok, net, detail = self._walk(
            self._legs(pos.legs, False),
            pos.qty,
            start,
            worst,
            client_id=f"otb-c-{uuid.uuid4().hex[:8]}",
        )
        if not ok:
            return Fill(False, 0.0, mid, detail)
        debit = net
        pnl = (pos.credit_received - debit) * 100 * pos.qty
        pos.status, pos.close_date, pos.close_debit, pos.realized_pnl, pos.close_reason = (
            "closed",
            today,
            debit,
            pnl,
            reason,
        )
        self._save()
        return Fill(True, debit, mid, detail)
