"""The model's seat at the table.

Scope, stated as narrowly as it is implemented:

* It sees the day's context and the gate results **after** the deterministic gates
  have already approved the trade.
* It may return `stand_down: true` to cancel the trade.
* It writes the journal narrative.

It cannot choose strikes, set size, change a limit price, place or cancel an order,
or loosen a gate. Those are arithmetic and risk limits, and there is no upside to
putting a language model between the risk limit and the order.

The asymmetry is the point: a wrong veto costs one day's premium, a wrong entry costs
the width of the spread. Only the cheap error is delegated.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any

from .config import AgentConfig

PROMPT = """You are reviewing a systematic 0DTE iron condor that has ALREADY passed every
deterministic risk gate. Python chose the strikes and the size; that is not your decision
and you cannot change it.

You have exactly one power: veto. Use it only for something the gates structurally cannot
see — a scheduled macro event inside the holding period, an obviously broken or stale
quote, a market state the numbers below describe implausibly. Absent such a reason, do not
veto. "Feels risky" is not a reason; the gates already priced the risk.

Session context:
{context}

Respond with JSON only, no prose outside it:
{{"stand_down": <true|false>, "reason": "<one sentence>", "note": "<2-4 sentences for the
trade journal: what the market offered today and what this structure is betting on>"}}
"""


@dataclass(frozen=True)
class Verdict:
    stand_down: bool
    reason: str
    note: str
    raw: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stand_down": self.stand_down,
            "reason": self.reason,
            "note": self.note,
            "error": self.error,
        }


def review(context: dict[str, Any], config: AgentConfig) -> Verdict | None:
    """Returns None when the model is disabled — the session then proceeds unreviewed."""
    if not config.llm.enabled:
        return None

    prompt = PROMPT.format(context=json.dumps(context, indent=2, sort_keys=True, default=str))
    try:
        proc = subprocess.run(
            config.llm.command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=config.llm.timeout_seconds,
            check=False,
        )
        if proc.returncode != 0:
            return _fallback(config, f"model command exited {proc.returncode}: {proc.stderr[:200]}")
        return _parse(proc.stdout, config)
    except (OSError, subprocess.SubprocessError) as exc:
        return _fallback(config, f"model command failed: {exc}")


def _parse(raw: str, config: AgentConfig) -> Verdict:
    text = raw.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return _fallback(config, "model returned no JSON object", raw)
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        return _fallback(config, f"model returned unparseable JSON: {exc}", raw)

    stand_down = payload.get("stand_down")
    if not isinstance(stand_down, bool):
        return _fallback(config, "model omitted a boolean stand_down", raw)

    return Verdict(
        stand_down=stand_down,
        reason=str(payload.get("reason", ""))[:500],
        note=str(payload.get("note", ""))[:2000],
        raw=raw[:4000],
    )


def _fallback(config: AgentConfig, error: str, raw: str | None = None) -> Verdict:
    """A broken reviewer must not silently become an approval, nor a permanent halt.

    `fail_open` decides which way it lands; either way the journal records that the
    review did not actually happen.
    """
    return Verdict(
        stand_down=not config.llm.fail_open,
        reason=f"review unavailable ({error})",
        note="No model review this session.",
        raw=raw,
        error=error,
    )
