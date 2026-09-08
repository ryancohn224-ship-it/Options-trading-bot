"""Streamlit dashboard: run `streamlit run src/otb/monitoring/dashboard.py -- --state state`.
Reads the ledger and risk state written by the live runner or a backtest."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl
import streamlit as st

state = Path(sys.argv[sys.argv.index("--state") + 1]) if "--state" in sys.argv else Path("state")
ledger = state / "ledger" if (state / "ledger").exists() else state
st.set_page_config(page_title="otb", layout="wide")
st.title("otb — options trading bot")

cols = st.columns(4)
eq = pl.read_parquet(ledger / "equity.parquet") if (ledger / "equity.parquet").exists() else None
if eq is not None and len(eq):
    e = eq["equity"].to_numpy()
    cols[0].metric("Equity", f"${e[-1]:,.0f}", f"{(e[-1] / e[0] - 1) * 100:+.2f}% since start")
    hw = pl.Series(e).cum_max().to_numpy()
    dd = 1 - e / hw
    cols[1].metric("Drawdown", f"{dd[-1] * 100:.2f}%", f"max {dd.max() * 100:.2f}%")
    cols[2].metric("Vol-target scalar", f"{eq['vol_scalar'][-1]:.2f}")
    st.line_chart(eq.to_pandas().set_index("date")[["equity"]])
    st.line_chart(eq.to_pandas().set_index("date")[["vol_scalar"]])
rs = state / "risk.json"
if rs.exists():
    r = json.loads(rs.read_text())
    cols[3].metric(
        "Risk",
        "KILLED" if r.get("killed") else "ok",
        r.get("kill_reason") or f"{r.get('consecutive_losses', 0)} consec. losses",
    )
    st.json(r, expanded=False)
tr = ledger / "trades.parquet"
if tr.exists():
    t = pl.read_parquet(tr)
    st.subheader("Open positions")
    st.dataframe(t.filter(pl.col("status") == "open").to_pandas())
    c = t.filter(pl.col("status") == "closed")
    st.subheader(f"Closed trades ({len(c)})")
    if len(c):
        wins = c.filter(pl.col("realized_pnl") > 0)["realized_pnl"].sum()
        losses = -c.filter(pl.col("realized_pnl") <= 0)["realized_pnl"].sum()
        st.write(
            {
                "win_rate": round(len(c.filter(pl.col("realized_pnl") > 0)) / len(c), 3),
                "profit_factor": round(wins / losses, 2) if losses else None,
                "by_reason": c.group_by("close_reason")
                .agg(pl.len(), pl.col("realized_pnl").sum())
                .to_dicts(),
            }
        )
        st.dataframe(
            c.select(
                [
                    "underlying",
                    "kind",
                    "open_date",
                    "close_date",
                    "qty",
                    "credit_received",
                    "close_debit",
                    "realized_pnl",
                    "close_reason",
                ]
            ).to_pandas()
        )
sl = ledger / "slippage.parquet"
if sl.exists():
    s = pl.read_parquet(sl)
    st.subheader("Slippage vs mid (the backtest's honesty check)")
    st.write(
        s.group_by("action")
        .agg(pl.col("slip_per_share").mean().alias("avg_slip"), pl.len())
        .to_dicts()
    )
ev = ledger / "events.parquet"
if ev.exists():
    st.subheader("Events")
    st.dataframe(pl.read_parquet(ev).tail(50).to_pandas())
