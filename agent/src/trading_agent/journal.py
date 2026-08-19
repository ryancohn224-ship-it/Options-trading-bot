"""The journal.

This is the agent's most important output — more than the P&L, which on any single
day is noise. Two formats, on purpose:

* `trades.jsonl` — one JSON object per session, for the weekly review script and for
  any later analysis. Machine-first.
* `YYYY-MM-DD.md` — the same session written for a human, including every gate that
  ran and the numbers behind it. If the agent stood down, this file says exactly what
  the market failed to offer.

A no-trade day gets a full entry. Cycles skipped is a headline statistic here, not an
absence of data: it is how you find out whether the credit gate is doing its job or
quietly strangling the strategy.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .models import GateReport


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


@dataclass
class JournalEntry:
    session_date: str
    session_id: str
    started_at: str
    mode: str
    config_hash: str
    git_sha: str
    outcome: str = "pending"  # traded | no_trade | halted | error
    underlying: str | None = None
    expiry: str | None = None
    spot: float | None = None
    prev_close: float | None = None
    atm_iv: float | None = None
    realized_vol: float | None = None
    expected_move: float | None = None
    preflight: list[dict[str, Any]] = field(default_factory=list)
    selection: str | None = None
    structure: dict[str, Any] | None = None
    gates: list[dict[str, Any]] = field(default_factory=list)
    sizing: str | None = None
    contracts: int = 0
    credit_per_contract: float | None = None
    max_loss_per_contract: float | None = None
    orders: list[dict[str, Any]] = field(default_factory=list)
    exit_reason: str | None = None
    realized_pnl: float | None = None
    llm: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.notes.append(text)


def gates_to_rows(report: GateReport) -> list[dict[str, Any]]:
    return [{"name": r.name, "passed": r.passed, "detail": r.detail} for r in report.results]


class Journal:
    def __init__(self, journal_dir: Path) -> None:
        self.dir = Path(journal_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, entry: JournalEntry) -> tuple[Path, Path]:
        """Append to the machine log, then rebuild the day's page from it.

        A trading day is several processes — one entry attempt, several management
        passes — and each writes a record. The daily page is rebuilt from every record
        carrying that date rather than overwritten by the last one, so the afternoon's
        exit cannot erase the morning's reasoning. `trades.jsonl` stays the source of
        truth; the markdown is a view of it.
        """
        jsonl = self.dir / "trades.jsonl"
        with jsonl.open("a") as fh:
            fh.write(json.dumps(asdict(entry), sort_keys=True) + "\n")

        same_day = [
            row
            for row in (json.loads(line) for line in jsonl.read_text().splitlines() if line.strip())
            if row.get("session_date") == entry.session_date
        ]
        md = self.dir / f"{entry.session_date}.md"
        md.write_text("\n---\n\n".join(render_markdown(JournalEntry(**row)) for row in same_day))
        return md, jsonl


def _gate_table(rows: list[dict[str, Any]]) -> str:
    lines = ["| | check | detail |", "|---|---|---|"]
    for row in rows:
        mark = "✅" if row["passed"] else "❌"
        lines.append(f"| {mark} | `{row['name']}` | {row['detail']} |")
    return "\n".join(lines) + "\n"


def render_markdown(entry: JournalEntry) -> str:
    if entry.exit_reason:
        headline = f"Closed ({entry.exit_reason})"
    else:
        headline = {
            "traded": "Opened",
            "no_trade": "Stood down",
            "halted": "Halted",
            "error": "Errored",
            "pending": "In progress",
        }.get(entry.outcome, entry.outcome)

    out = [
        f"# {entry.session_date} — {headline}",
        "",
        f"`{entry.mode}` · session `{entry.session_id}` · config `{entry.config_hash}` "
        f"· git `{entry.git_sha}` · started {entry.started_at}",
        "",
    ]

    if entry.spot is not None:
        market = [f"**Market.** {entry.underlying} at {entry.spot:.2f}"]
        if entry.prev_close:
            market.append(f"prior close {entry.prev_close:.2f} ({entry.spot / entry.prev_close - 1:+.2%})")
        if entry.atm_iv:
            market.append(f"ATM IV {entry.atm_iv:.1%}")
        if entry.realized_vol:
            market.append(f"realized {entry.realized_vol:.1%}")
        if entry.expected_move:
            market.append(f"expected move ±{entry.expected_move:.2f}")
        out += [", ".join(market) + ".", ""]

    # Management passes have no preflight or gates of their own; rendering empty
    # sections for them would bury the parts of the page that do carry information.
    if entry.preflight:
        out += ["## Preflight", "", _gate_table(entry.preflight), ""]

    if entry.selection:
        out += ["## Selection", "", entry.selection, ""]

    if entry.structure:
        s = entry.structure
        out += [
            "## Structure",
            "",
            f"```\n"
            f"  buy  {s['long_call']:>8.2f} C\n"
            f"  sell {s['short_call']:>8.2f} C\n"
            f"       {'-' * 12}   spot {entry.spot:.2f}\n"
            f"  sell {s['short_put']:>8.2f} P\n"
            f"  buy  {s['long_put']:>8.2f} P\n"
            f"```",
            "",
            f"Width {s['width']:.2f} · conservative credit {s['net_credit']:.2f} "
            f"({s['credit_ratio']:.1%} of width) · mid credit {s['mid_credit']:.2f} · "
            f"breakevens {s['breakeven_low']:.2f} / {s['breakeven_high']:.2f}",
            "",
        ]

    if entry.gates:
        out += ["## Credit gate", "", _gate_table(entry.gates), ""]

    if entry.sizing:
        out += ["## Sizing", "", entry.sizing, ""]

    if entry.llm:
        verdict = "STAND DOWN" if entry.llm.get("stand_down") else "no objection"
        out += [
            "## Model review",
            "",
            f"**{verdict}** — {entry.llm.get('reason', '(no reason given)')}",
            "",
            entry.llm.get("note", ""),
            "",
        ]

    if entry.orders:
        out += ["## Orders", ""]
        for order in entry.orders:
            out.append(
                f"- `{order.get('kind')}` {order.get('order_id')} — {order.get('status')} "
                f"@ {order.get('limit')} → {order.get('detail', '')}"
            )
        out.append("")

    if entry.outcome == "traded" or entry.realized_pnl is not None:
        out += [
            "## Result",
            "",
            f"- Contracts: {entry.contracts}",
            f"- Credit per contract: {entry.credit_per_contract:.2f}"
            if entry.credit_per_contract is not None
            else "- Credit per contract: n/a",
            f"- Max loss per contract: {entry.max_loss_per_contract:.0f}"
            if entry.max_loss_per_contract is not None
            else "- Max loss per contract: n/a",
            f"- Exit: {entry.exit_reason or 'n/a'}",
            f"- Realized P&L: {entry.realized_pnl:+.2f}"
            if entry.realized_pnl is not None
            else "- Realized P&L: n/a",
            "",
        ]

    if entry.notes:
        out += ["## Notes", ""] + [f"- {n}" for n in entry.notes] + [""]

    return "\n".join(out)


def new_entry(session_date: date, mode: str, config_hash: str, now: datetime) -> JournalEntry:
    return JournalEntry(
        session_date=session_date.isoformat(),
        session_id=f"{session_date.isoformat()}-{now:%H%M%S}",
        started_at=now.isoformat(timespec="seconds"),
        mode=mode,
        config_hash=config_hash,
        git_sha=git_sha(),
    )
