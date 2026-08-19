"""The agent's memory.

Two files, both plain JSON, both written atomically and both meant to be committed:

* `agent_state.json` — what the agent must remember between sessions: whether it has
  already traded today, how many losing days are behind it, whether the kill switch
  is set and why.
* `positions.json`  — the open structure, so a crashed process can be restarted
  mid-session and still know what it is holding.

Restart-safety is the whole point. A scheduled agent that forgets its position when
the box reboots is short four legs it does not know about.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class OpenPosition:
    session_date: str
    underlying: str
    expiry: str
    contracts: int
    credit_per_contract: float
    short_put: float
    long_put: float
    short_call: float
    long_call: float
    entry_order_id: str
    entry_time: str
    max_loss_per_contract: float

    @property
    def credit_dollars(self) -> float:
        return self.credit_per_contract * 100.0 * self.contracts


@dataclass
class AgentState:
    last_session_date: str | None = None
    entries_today: int = 0
    realized_pnl_today: float = 0.0
    consecutive_loss_days: int = 0
    kill_switch: str | None = None
    kill_switch_set_at: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def roll_to(self, today: date) -> None:
        """Start a new session day, carrying forward only what should survive it."""
        key = today.isoformat()
        if self.last_session_date == key:
            return
        if self.last_session_date is not None:
            self.history.append(
                {
                    "date": self.last_session_date,
                    "entries": self.entries_today,
                    "realized_pnl": round(self.realized_pnl_today, 2),
                }
            )
            self.history = self.history[-90:]
            if self.realized_pnl_today < 0:
                self.consecutive_loss_days += 1
            elif self.realized_pnl_today > 0:
                self.consecutive_loss_days = 0
        self.last_session_date = key
        self.entries_today = 0
        self.realized_pnl_today = 0.0
        # A daily-loss halt expires with the day it was set for. A halt that needs a
        # human (consecutive losses) is re-asserted by the guardrails on the next run.
        if self.kill_switch and self.kill_switch.startswith("daily_loss"):
            self.kill_switch = None
            self.kill_switch_set_at = None

    def trip(self, reason: str) -> None:
        self.kill_switch = reason
        self.kill_switch_set_at = datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.state_path = self.state_dir / "agent_state.json"
        self.position_path = self.state_dir / "positions.json"

    def load_state(self) -> AgentState:
        if not self.state_path.exists():
            return AgentState()
        return AgentState(**json.loads(self.state_path.read_text()))

    def save_state(self, state: AgentState) -> None:
        _atomic_write(self.state_path, json.dumps(asdict(state), indent=2, sort_keys=True))

    def load_position(self) -> OpenPosition | None:
        if not self.position_path.exists():
            return None
        raw = json.loads(self.position_path.read_text())
        return OpenPosition(**raw) if raw else None

    def save_position(self, position: OpenPosition | None) -> None:
        _atomic_write(
            self.position_path,
            json.dumps(asdict(position) if position else None, indent=2, sort_keys=True),
        )
