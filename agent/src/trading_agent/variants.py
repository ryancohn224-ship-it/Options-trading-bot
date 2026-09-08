"""Variants: named parameter overlays that compete against the champion.

One trade a day is not enough evidence to tune anything. But every stored day can be
replayed through as many parameter sets as you like, so the scarce resource is not
market days — it is *ideas*, and each idea gets tested against the entire history the
moment it is written down.

A variant is a partial config. It is deep-merged into the base and then re-validated,
which means a variant inherits every bound in `config.py`: it cannot propose a 45-delta
short strike or a 30%-of-equity position any more than a hand-edited YAML file can. The
research loop is allowed to search inside the risk limits, never through them.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from .config import AgentConfig

Status = Literal["champion", "challenger", "retired"]


@dataclass(frozen=True)
class Variant:
    name: str
    overrides: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    status: Status = "challenger"
    #: Set when a challenger is retired, so the reasoning survives the decision.
    retired_reason: str = ""

    def apply_to(self, base: AgentConfig) -> AgentConfig:
        merged = deep_merge(base.model_dump(mode="json"), self.overrides)
        return AgentConfig.model_validate(merged)


def deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


DEFAULT_VARIANTS: tuple[Variant, ...] = (
    Variant("champion", {}, "The live configuration. Whatever is trading today.", "champion"),
    Variant(
        "wider-wings",
        {"structure": {"wing_width": 2.0}},
        "Wider wings collect more absolute credit and raise credit-to-width, at more "
        "risk per contract. Tests whether the $1 wing is too tight to clear costs.",
    ),
    Variant(
        "further-out",
        {"structure": {"short_delta": 0.10}},
        "Sell further from the money: fewer losses, thinner credit. The other end of "
        "the same trade-off the 16-delta default picks a point on.",
    ),
    Variant(
        "closer-in",
        {"structure": {"short_delta": 0.22}},
        "Nearer strikes for materially more credit. Should lose more often; the "
        "question is whether the extra premium more than pays for it.",
    ),
    Variant(
        "no-vrp-gate",
        {"gate": {"min_vrp": 0.0}},
        "The control for the edge claim. If this matches the champion, the variance "
        "risk premium filter is doing nothing and the champion's edge is elsewhere.",
    ),
    Variant(
        "strict-vrp",
        {"gate": {"min_vrp": 0.30}},
        "Trade only when implied is far above realized. Fewer trades; the test is "
        "whether expectancy per trade rises enough to justify the ones skipped.",
    ),
    Variant(
        "take-quarter",
        {"exits": {"profit_target": 0.25}},
        "Take a quarter of the credit and leave. Cuts tail exposure and raises win "
        "rate; the risk is paying two spreads for a quarter of a thin credit.",
    ),
    Variant(
        "take-three-quarters",
        {"exits": {"profit_target": 0.75}},
        "Hold for most of the credit. Fewer round trips, more time exposed to a move.",
    ),
    Variant(
        "tight-stop",
        {"exits": {"stop_loss_fraction": 0.35}},
        "Cut at a third of max loss. On a defined-risk structure a stop swaps a capped "
        "loss for a smaller certain one — worth testing, not obvious.",
    ),
    Variant(
        "no-stop",
        {"exits": {"stop_loss_fraction": 1.0}},
        "Never stop; let the defined risk do its job and flatten at 15:45. The control "
        "for whether stopping helps at all on a structure that cannot lose more than "
        "its width.",
    ),
    Variant(
        "late-entry",
        {"schedule": {"entry_start": "11:00", "entry_cutoff": "13:00"}},
        "Enter later: less time exposed, less premium. Tests whether the 10:00 start "
        "is buying decay or buying risk.",
    ),
)


def load_variants(path: str | Path | None = None) -> list[Variant]:
    if path is None or not Path(path).exists():
        return list(DEFAULT_VARIANTS)
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return [Variant(**row) for row in raw.get("variants", [])]


def save_variants(variants: list[Variant], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "variants": [
            {k: v for k, v in vars(variant).items() if v not in ({}, "")}
            | {"name": variant.name, "status": variant.status}
            for variant in variants
        ]
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False, width=100))


def active(variants: list[Variant]) -> list[Variant]:
    return [v for v in variants if v.status != "retired"]


def champion(variants: list[Variant]) -> Variant:
    for variant in variants:
        if variant.status == "champion":
            return variant
    return variants[0]
